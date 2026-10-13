"""Regression tests for the execution engine's first independent review (C2-1 to C2-11).

Each reviewer probe is a test named after its finding. Every test fails on the
behaviour the finding described: the probe's scenario, with the fixed
expectation. The review's other requests (a commission report before its
execution, a fill rewritten every tick, the bridge interlock) are at the end.
"""
from __future__ import annotations

import ast
import copy
from decimal import Decimal
from pathlib import Path
import re
import sqlite3
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fakes.engine_harness import ACCOUNT, CLIENT_ID, Clock, Harness, build, ny, promote, stepping
from trading.execution import live_executor
from trading.execution.live_executor import (
    BRIDGE_PROCESS_PATTERN,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_RESTART,
    ExecutionRefused,
    parse_completed_time,
    running_bridge_processes,
)
from trading.execution.promotion import ENGINE_SOURCE_FILES, EngineAuthority, ExecutionAuthorityError


HELD = 300
BACKEND = Path(__file__).resolve().parents[1]


async def open_bracket(h: Harness, symbol: str = "HELD", con_id: int = HELD, *, quantity: int = 100,
                       filled: int | None = None, price: str = "50.00") -> Any:
    """A bracket sent through the engine whose entry filled (all of it unless ``filled``)."""
    await h.decide(symbol, con_id=con_id, quantity=quantity)
    await h.engine.tick()
    bracket = h.bracket(symbol)
    h.tws.fill(bracket.order_id("P"), quantity if filled is None else filled, price)
    await h.engine.tick()
    h.events()
    return bracket


def sells(h: Harness, *, after: int = 3) -> list[tuple[int, str, str, int]]:
    """placeOrder calls after the bracket's three: modifies of its legs and fallback sells."""
    return [(oid, sent.action, sent.orderType, int(sent.totalQuantity)) for oid, sent in h.tws.placed[after:]]


def owner_sells(h: Harness, quantity: int, *, status: str = "Submitted", order_id: int = 7) -> Any:
    """The owner's SELL in the TWS GUI (client 0): working, or already filled."""
    return h.tws.add_foreign_order(client_id=0, order_id=order_id, con_id=HELD, symbol="HELD", action="SELL",
                                   quantity=quantity, order_type="LMT", price=49.95, status=status)


# ---- C2-1 (HIGH): a filled sell the position does not show yet is never sold again ----------------


async def test_c2_1_a_filled_owner_sell_the_position_does_not_show_yet_is_never_sold_again(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])  # the target is gone: the flatten needs the fallback
    # The owner closed all 100 by hand: TWS reports the order Filled, its position update has not come yet.
    owner_sells(h, 100, status="Filled")
    h.clock.value = ny(15, 45, 1)
    h.events()
    await h.engine.tick()

    assert sells(h) == []  # the review's probe sold 100 more: the account would have ended short 100
    assert h.tws.cancelled == []  # and the stop is never cancelled for a sell that cannot be sent
    assert bracket.flatten_state == "skipped"
    assert h.critical() == [
        "HELD: account holds 100 sh with 100 sold by other orders that the position does not show yet — no sell"
        " for the 100 sh the engine expects (its stop stays live); act in TWS if not flat"
    ]

    h.clock.advance(16)  # the bridge's L-e: still not shown 15 s later is CRITICAL, the shares stay reserved
    await h.engine.tick()
    assert h.critical() == [
        "HELD: the account position (100 sh) still does not show 100 sh other orders sold 16s ago — those shares"
        " stay reserved and the engine never sells them; check TWS"
    ]
    h.tws.move_position(HELD, -100)  # the position update arrives: nothing is reserved any more
    await h.engine.tick()
    assert h.engine.unshown_since == {}


@pytest.mark.parametrize("position_shown", [False, True], ids=["position-trails", "position-shows-it"])
async def test_c2_1_a_partly_filled_owner_sell_is_reserved_until_the_position_shows_it(
    tmp_path, monkeypatch, position_shown: bool
) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    owner_sells(h, 40, status="Filled")
    if position_shown:
        h.tws.move_position(HELD, -40)
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    # Either way the account ends flat: 40 sold by the owner, 60 by the engine's one market sell.
    assert sells(h) == [(103, "SELL", "MKT", 60)]
    assert h.tws.cancelled == [102]


@pytest.mark.parametrize("position_shown", [False, True], ids=["position-trails", "position-shows-it"])
async def test_c2_1_a_partly_filled_working_owner_sell_reserves_its_rest_and_its_unshown_fills(
    tmp_path, monkeypatch, position_shown: bool
) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    working = owner_sells(h, 50)
    working.filled = 20  # 20 of the owner's 50 executed; 30 still work
    if position_shown:
        h.tws.move_position(HELD, -20)
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    # The owner's order sells 50 in all: 20 done, 30 to come. The engine sells the other 50, never more.
    assert sells(h) == [(103, "SELL", "MKT", 50)]


async def test_c2_1_owner_sells_before_the_entry_reserve_nothing(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    h.clock.value = ny(9, 40)
    owner_sells(h, 100, status="Filled")  # the owner sold an overnight holding; the account was flat at entry
    h.clock.value = ny(10, 0, 5)
    await h.engine.start()
    await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert sells(h) == [(101, "SELL", "LMT", 100)]  # the whole position is repriced: nothing is reserved


@pytest.mark.parametrize("target_live", [True, False], ids=["reprice", "fallback"])
async def test_c2_1_without_a_fresh_snapshot_nothing_is_sold(tmp_path, monkeypatch, target_live: bool) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    if not target_live:
        h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.answer_snapshots = False  # TWS does not answer: what other clients sold is unknown
    h.clock.value = ny(15, 45, 1)
    h.events()
    await h.engine.tick()

    assert sells(h) == [] and h.tws.cancelled == []  # in doubt: alert, never sell
    assert bracket.flatten_state == "none"  # it waits for a fresh snapshot (re-review R-1)
    assert ("HELD: flatten waits for a fresh TWS snapshot — nothing sent; tried again every tick until 15:50 ET"
            in h.critical())


@pytest.mark.parametrize(("text", "expected"), [
    ("20261013 15:44:02 America/New_York", ny(15, 44, 2)),
    ("20261013-15:44:02 US/Eastern", ny(15, 44, 2)),
    ("20261013 15:44:02 EDT", ny(15, 44, 2)),
    ("20261013 21:44:02 Asia/Jerusalem", ny(14, 44, 2)),
    ("20261013 15:44:02", None),  # no zone: cannot be placed in time
    ("20261013 15:44:02 XYZT", None),  # a zone this machine cannot resolve
    ("", None),
    ("not a time", None),
])
def test_c2_1_completion_times_are_trusted_only_with_a_known_zone(text: str, expected: Any) -> None:
    assert parse_completed_time(text) == expected


async def test_c2_1_a_sell_with_an_unreadable_completion_time_counts_as_after_the_entry(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    sale = owner_sells(h, 100, status="Filled")
    sale.completed_at = "20261013 15:44:02"  # no zone: it may have been after the entry
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert sells(h) == [] and h.tws.cancelled == []


async def test_c2_1_a_short_in_a_symbol_the_engine_traded_is_critical_and_never_reported_flat(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    owner_sells(h, 100)
    h.tws.fill(7, 100, "50.10", client_id=0)  # the owner sold the bot's shares by hand
    h.tws.fill(101, 100, "51.00")  # and the target filled too: the account is short 100
    h.clock.advance(121)
    await h.engine.tick()

    assert bracket.state == "closed"
    assert "short:300" in h.engine.findings  # new entries pause
    critical = h.critical()
    assert "HELD: account is short 100 sh in a symbol the engine traded today — act in TWS" in critical
    assert "reconciliation mismatch (short_position) — new entries paused; exits continue" in critical

    h.clock.value = ny(15, 50, 30)
    await h.engine.tick()
    out = h.events()
    assert ("CRITICAL", "HELD: account is short 100 sh in a symbol the engine traded today — act in TWS") in out
    assert not [text for kind, text in out if kind == "FLAT"]  # the review's probe reported FLAT here


# ---- C2-2: order ids skip every id TWS reports for this client --------------------------------------


async def test_c2_2_new_order_ids_skip_a_live_order_the_ledger_does_not_know(tmp_path, monkeypatch) -> None:
    # A GTC stop of this client id that is not in the ledger (left by a drill script) is live as order
    # 100, and TWS's sequence was reset so nextValidId is 100 again.
    h = await build(tmp_path, monkeypatch, start=False, next_valid_id=100)
    foreign = h.tws.add_foreign_order(client_id=CLIENT_ID, order_id=100, con_id=999, symbol="OLD", action="SELL",
                                      quantity=50, order_type="STP", price=10.0, status="PreSubmitted")
    await h.engine.start()
    await h.decide("NEW", con_id=7001)
    await h.engine.tick()

    assert h.placed_ids() == [101, 102, 103]  # placeOrder on 100 would have modified the foreign stop
    assert (foreign.order.action, foreign.order.orderType, foreign.order.totalQuantity) == ("SELL", "STP", 50)
    assert ("INFO", "next order id raised from 100 to 101: ids up to 100 are in use at TWS") in h.events()


async def test_c2_2_order_ids_stay_above_the_ledger_when_tws_reports_none_of_its_orders(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("AAPL", con_id=7001)
    await h.engine.tick()
    h.tws.cancelOrder(100)  # the whole bracket ends unfilled: TWS will report no open order and no execution
    await h.engine.tick()
    h.tws.restart_tws(next_valid_id=1)
    restarted = await h.restart()
    await restarted.decide("TSLA", con_id=7002)
    await restarted.engine.tick()

    assert h.placed_ids() == [100, 101, 102, 103, 104, 105]  # only the ledger still knows 100-102


# ---- C2-3: a slow TWS is transient (exit 75), an unsafe one permanent (exit 78) ---------------------


async def test_c2_3_a_slow_tws_exits_75_for_a_supervised_restart(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    h.engine.mirror.begin_connection()  # connected, but TWS has not sent managedAccounts yet
    assert await h.engine.run() == EXIT_RESTART
    assert h.critical() == ["TWS not ready: accounts_unknown: TWS reported no managed accounts — exit 75 for a"
                            " supervised restart"]

    slow = await build(tmp_path / "ids", monkeypatch, start=False)
    slow.tws.reqIds = lambda numIds: None  # type: ignore[method-assign]  # nextValidId slow to arrive
    slow.engine.mirror.next_id = None
    assert await slow.engine.run() == EXIT_RESTART

    live = await build(tmp_path / "live", monkeypatch, start=False, accounts=f"{ACCOUNT},U7654321")
    assert await live.engine.run() == EXIT_REFUSED  # a non-paper account is never retried


# ---- C2-4: the target reprice leaves other sells their shares -----------------------------------------


async def test_c2_4_the_reprice_never_sells_next_to_the_owners_working_close(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    owner_sells(h, 100)  # working in TWS
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(15, 45, 1)
    h.events()
    await h.engine.tick()

    assert sells(h) == []  # the probe made the target marketable for all 100 next to the owner's close
    assert h.tws.cancelled == [] and bracket.flatten_state == "skipped"
    assert h.critical() == [
        "HELD: account holds 100 sh with 100 reserved by working sells — no sell for the 100 sh the engine"
        " expects (its stop stays live); act in TWS if not flat"
    ]


async def test_c2_4_the_reprice_is_limited_to_what_other_sells_leave_and_resends_keep_it(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    owner_sells(h, 40)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.modify_mode = "silent"  # TWS never confirms: the reprice is re-sent, then the fallback takes over
    h.clock.value = ny(15, 45, 1)
    h.events()
    for _ in range(7):
        await h.engine.tick()
        h.clock.advance(2)

    assert sells(h)[:4] == [(101, "SELL", "LMT", 60)] * 4
    assert sells(h)[4:] == [(103, "SELL", "MKT", 60)]
    assert bracket.flatten_state == "selling"
    out = h.events()
    assert ("FLATTEN", "HELD: target repriced to 49.94 for 60 sh (bid 50.2)") in out
    assert ("CRITICAL", "HELD: account holds 100 sh, 40 of them reserved by other sells, but the bracket expects"
            " 100 — flatten of target 101 limited to 60 sh; act in TWS if they do not fill") in out


async def test_c2_4_a_reprice_sent_before_a_restart_is_never_resent_with_a_size_decided_afresh(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    owner_sells(h, 40)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.modify_mode = "silent"
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert sells(h) == [(101, "SELL", "LMT", 60)]

    restarted = await h.restart(clock=Clock(ny(15, 45, 10)))
    restarted.events()
    for _ in range(2):
        await restarted.engine.tick()

    # Re-sending from the position alone would have made the target marketable for all 100.
    assert sells(h) == [(101, "SELL", "LMT", 60), (103, "SELL", "MKT", 60)]
    assert sorted(h.tws.cancelled) == [101, 102]
    assert ("HELD: TWS does not show the flatten of target 101 sent before the restart (49.94) — not re-sent;"
            " act in TWS") in restarted.critical()


async def test_c2_4_a_reprice_tws_applied_before_a_restart_is_confirmed_by_its_price(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    owner_sells(h, 40)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.modify_mode = "silent"
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    target = h.tws.orders[(CLIENT_ID, 101)]
    target.order = copy.copy(h.tws.placed[-1][1])  # TWS applied it; its echo was lost with the engine

    restarted = await h.restart(clock=Clock(ny(15, 45, 10)))
    for _ in range(2):
        await restarted.engine.tick()

    assert sells(h) == [(101, "SELL", "LMT", 60)] and h.tws.cancelled == []  # TWS shows 60 @ 49.94: done
    assert restarted.bracket("HELD").flatten_state == "repriced"


# ---- C2-5: the fallback cancels an Inactive leg and sells only once it is cancelled -----------------


@pytest.mark.parametrize("confirmed", [True, False], ids=["cancel-confirmed", "cancel-pending"])
async def test_c2_5_the_fallback_cancels_an_inactive_stop_before_it_sells(
    tmp_path, monkeypatch, confirmed: bool
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    stop = h.tws.orders[(CLIENT_ID, 102)]
    stop.status = "Inactive"  # an Inactive order can come back: it is not dead
    h.tws.status_only(stop)
    h.tws.confirm_cancels = confirmed
    h.clock.value = ny(15, 45, 1)
    for _ in range(18):
        await h.engine.tick()
        h.clock.advance(2)

    assert h.tws.cancelled[0] == 102  # the probe sold next to the Inactive stop it never cancelled
    if confirmed:
        assert sells(h) == [(103, "SELL", "MKT", 100)]
    else:
        assert sells(h) == [] and bracket.flatten_state == "failed"


# ---- C2-6: an entry whose stop died is cancelled and flagged ------------------------------------------


@pytest.mark.parametrize("cancellable", [True, False], ids=["entry-cancelled", "entry-foreign-ref"])
async def test_c2_6_a_working_entry_whose_stop_died_is_cancelled_and_flagged(
    tmp_path, monkeypatch, cancellable: bool
) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("WORKS", con_id=7300)
    await h.engine.tick()
    bracket = h.bracket("WORKS")
    parent = h.tws.orders[(CLIENT_ID, bracket.order_id("P"))]
    if not cancellable:  # TWS reports another orderRef on the entry's id: the engine never cancels it
        parent.order.orderRef = "manual:owner-1"
        h.tws.report(parent)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, bracket.order_id("S"))])  # the stop was rejected or cancelled
    h.events()
    for _ in range(2):
        h.clock.advance(121)
        await h.engine.tick()

    critical = h.critical()
    if cancellable:
        assert h.tws.cancelled == [100] and bracket.state == "cancelled"
        assert critical[0] == ("entry WORKS: stop 102 is Cancelled while the entry works — entry cancelled (filled"
                               " 0/100); act in TWS")
        assert not h.engine.findings
    else:
        assert h.tws.cancelled == [] and f"stop-dead:{bracket.id}" in h.engine.findings  # entries pause
        assert "WORKS: stop 102 is Cancelled while the entry works — check TWS" in critical


# ---- C2-7: a start after the stop time sends nothing -------------------------------------------------


async def test_c2_7_a_start_after_the_stop_time_sends_nothing_and_reports_the_ledger(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.20}
    clock = Clock(ny(17, 30))
    restarted = await h.restart(start=False, clock=clock)
    restarted.engine.pause = stepping(clock, 30)
    restarted.events()
    requests = len(h.tws.requests)

    assert await restarted.engine.run() == EXIT_OK

    assert sells(h) == [] and len(h.tws.requests) == requests  # the probe repriced the target at 17:30
    assert restarted.events() == [
        ("INFO", "started after 15:55 ET for 2026-10-13 — nothing new is managed"),
        ("CRITICAL", "HELD: still open per the execution ledger (100 sh) — act in TWS"),
    ]


async def test_c2_7_no_late_flatten_is_sent_after_the_close(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(16, 1)  # the loop only reached its stop checkpoint after the close
    h.events()

    assert await h.engine.finish() == EXIT_OK

    assert sells(h) == []
    out = h.events()
    assert ("INFO", "past the 16:00 ET close — no late flatten is sent") in out
    assert ("CRITICAL", "HELD: still open after flatten (100 sh) — act in TWS") in out


# ---- C2-8: the orderRef guards ------------------------------------------------------------------------


async def test_c2_8_an_order_carrying_another_orderref_is_never_cancelled(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("WORKS", con_id=7300)
    await h.engine.tick()
    bracket = h.bracket("WORKS")
    parent = h.tws.orders[(CLIENT_ID, bracket.order_id("P"))]
    parent.order.orderRef = "manual:owner-9"  # TWS reports another order on the entry's id
    h.tws.report(parent)
    h.events()
    h.clock.advance(601)  # the entry expires after 10 minutes
    await h.engine.tick()

    assert h.tws.cancelled == []
    assert (f"WORKS: order 100 at IBKR carries manual:owner-9, not at1:{bracket.id}:P — not cancelled; act in TWS"
            in h.critical())


async def test_c2_8_an_execution_whose_order_id_contradicts_its_orderref_is_not_counted(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("WORKS", con_id=7300)
    await h.engine.tick()
    bracket = h.bracket("WORKS")
    ref = bracket.legs["P"].order_ref
    h.tws.add_foreign_order(client_id=CLIENT_ID, order_id=555, con_id=7300, symbol="WORKS", action="BUY",
                            quantity=100, order_ref=ref)
    execution = h.tws.fill(555, 100, "50.00", order_ref=ref)
    h.events()
    await h.engine.tick()

    assert bracket.entry_filled == 0
    assert (f"execution {execution.execId} carries {ref} but order 555, not 100 — not counted; check TWS"
            in h.critical())


# ---- C2-9: no order id for the fallback sell after the cancels -------------------------------------------


@pytest.mark.parametrize("failure", ["ledger", "no_order_id"])
async def test_c2_9_a_fallback_sell_without_an_order_id_is_critical_once_and_retried(
    tmp_path, monkeypatch, failure: str
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    ledger_max = h.engine.ledger.max_order_id

    async def locked(client_id: int) -> int:
        raise sqlite3.OperationalError("database is locked")

    if failure == "ledger":
        h.engine.ledger.max_order_id = locked  # type: ignore[method-assign]
    else:
        h.mirror.next_id = None
    h.clock.value = ny(15, 45, 1)
    for _ in range(3):
        await h.engine.tick()
        h.clock.advance(2)

    assert sells(h) == [] and h.tws.cancelled == [102]  # the stop is gone: the engine must keep trying
    assert bracket.flatten_state == "cancelling"
    critical = h.critical()
    assert len([text for text in critical if "fallback market sell not sent yet (no order id" in text]) == 1

    h.engine.ledger.max_order_id = ledger_max  # type: ignore[method-assign]
    h.tws.reqIds(-1)
    await h.engine.tick()
    assert sells(h) == [(103, "SELL", "MKT", 100)]
    await h.engine.tick()
    assert len(sells(h)) == 1


# ---- C2-10: TWS error 1101 renews the subscriptions -------------------------------------------------------


async def test_c2_10_tws_error_1101_renews_the_position_and_account_subscriptions(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    h.events()
    h.tws.requests.clear()
    h.tws.error(-1, 1101, "Connectivity between IB and Trader Workstation has been restored - data lost.")
    assert h.critical() == ["TWS -1 1101 Connectivity between IB and Trader Workstation has been restored - data"
                            " lost."]

    await h.engine.tick()

    assert not h.mirror.data_lost.is_set()
    for request in ("cancelAccountSummary", "reqAccountSummary", "cancelPositions", "reqPositions"):
        assert request in h.tws.requests, request
    assert ("INFO", "TWS lost its data subscriptions (1101): positions and net liquidation requested again;"
            " reconciling") in h.events()


# ---- C2-11: the promoted code is the code that runs ----------------------------------------------------


def _trading_imports(relative: str) -> set[str]:
    """Every trading module a source file imports (top level or inside a function), as backend paths."""
    found: set[str] = set()
    for node in ast.walk(ast.parse((BACKEND / relative).read_text())):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [f"{node.module}.{alias.name}" for alias in node.names] + [node.module]
        for module in modules:
            path = Path(*module.split(".")).with_suffix(".py")
            if module.startswith("trading.") and (BACKEND / path).is_file():
                found.add(path.as_posix())
    return found


def test_c2_11_the_code_digest_covers_every_trading_module_the_engine_imports() -> None:
    for required in ("trading/storage/runtime_fence.py", "trading/execution/manual_orders.py",
                     "trading/scheduler/sessions.py"):
        assert required in ENGINE_SOURCE_FILES
    from trading.execution.promotion import ENGINE_PROCESS_FILES

    # The process entry files are bound as files; their other commands' imports never run in the
    # engine's process (tests/test_live_executor_rereview.py walks what does).
    for relative in set(ENGINE_SOURCE_FILES) - set(ENGINE_PROCESS_FILES):
        missing = _trading_imports(relative) - set(ENGINE_SOURCE_FILES)
        assert not missing, f"{relative} imports {sorted(missing)}, which the promotion does not bind"


async def test_c2_11_the_engine_refuses_an_authority_for_other_code(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    other = "0" * 64
    await promote(code_digest=other)  # a grant for another build of the engine
    h.engine.authority = EngineAuthority(account=ACCOUNT, code_digest=other)

    with pytest.raises(ExecutionAuthorityError) as refused:
        await h.engine.start()
    assert refused.value.code == "code_mismatch"
    assert await h.engine.run() == EXIT_REFUSED
    assert h.tws.placed == []


async def test_c2_11_the_engine_refuses_when_its_files_changed_after_it_was_loaded(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    monkeypatch.setattr(live_executor, "LOADED_ENGINE_DIGEST", "f" * 64)

    with pytest.raises(ExecutionAuthorityError) as refused:
        await h.engine.start()
    assert refused.value.code == "code_mismatch"
    assert "changed after this process loaded them" in str(refused.value)


# ---- review requests: commissions, saved fills, the bridge interlock ---------------------------------------


async def test_a_commission_report_that_arrives_before_its_execution_is_kept(tmp_path, monkeypatch) -> None:
    from tests.test_live_executor_entries import db_rows

    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    exec_id = "0000e0d5.000000ff.01.01"
    h.mirror.commissionReport(SimpleNamespace(execId=exec_id, commission=1.25, currency="USD"))
    h.tws.fill(101, 100, "51.00", commission=None, exec_id=exec_id)
    await h.engine.tick()

    assert db_rows("SELECT commission FROM autotrader_exec_fills WHERE exec_id=?", (exec_id,)) == [("1.25",)]
    assert bracket.commissions == Decimal("2.25")
    assert bracket.realized_pnl == Decimal("97.75")


async def test_saved_fills_are_never_written_again_every_tick(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    record_fills = h.engine.ledger.record_fills
    writes: list[list[str]] = []

    async def counted(records: list[Any]) -> Any:
        writes.append([record.exec_id for record in records])
        return await record_fills(records)

    h.engine.ledger.record_fills = counted  # type: ignore[method-assign]
    for _ in range(3):
        h.clock.advance(1)
        await h.engine.tick()
    assert writes == []  # the entry's fill and its commission are stored: nothing to write

    h.tws.fill(101, 40, "50.50")
    for _ in range(3):
        h.clock.advance(1)
        await h.engine.tick()
    assert len(writes) == 1 and len(writes[0]) == 1


def read_heartbeats_from_the_ledger(h: Harness) -> None:
    """The test bridge writes its heartbeat into the test ledger (the real bridge's is never read here)."""
    from config import cfg

    h.engine.bridge_database = Path(cfg.DB_PATH)
    h.engine.bridge_heartbeat = h.engine.read_bridge_heartbeat  # the engine's own reader (the harness stubs it)


async def test_the_engine_never_starts_while_the_bridge_runs(tmp_path, monkeypatch) -> None:
    from trading.execution.manual_orders import write_bridge_heartbeat

    h = await build(tmp_path, monkeypatch, start=False)
    read_heartbeats_from_the_ledger(h)
    h.engine.bridge_processes = lambda: [(4242, "python scripts_local/paper_bridge.py --date 2026-10-13")]
    with pytest.raises(ExecutionRefused) as refused:
        await h.engine.start()
    assert refused.value.code == "bridge_running" and "pid 4242" in str(refused.value)
    assert await h.engine.run() == EXIT_REFUSED

    def unreadable() -> list[tuple[int, str]]:
        raise OSError("pgrep exited 3")

    h.engine.bridge_processes = unreadable  # in doubt: as if the bridge were running
    with pytest.raises(ExecutionRefused):
        await h.engine.start()

    h.engine.bridge_processes = lambda: []
    await write_bridge_heartbeat("running", now=h.clock.value, pid=4242)
    with pytest.raises(ExecutionRefused) as refused:
        await h.engine.start()
    assert "heartbeat 0s ago" in str(refused.value)

    h.clock.advance(61)  # an old heartbeat: the bridge has stopped
    await h.engine.start()
    assert h.engine.started and h.tws.placed == []


async def test_a_bridge_that_appears_later_pauses_new_entries(tmp_path, monkeypatch) -> None:
    from trading.execution.manual_orders import write_bridge_heartbeat

    h = await build(tmp_path, monkeypatch)
    read_heartbeats_from_the_ledger(h)
    h.clock.advance(121)
    await write_bridge_heartbeat("running", now=h.clock.value, pid=4242)
    await h.decide("NEW", con_id=7001)
    h.events()
    await h.engine.tick()

    assert h.tws.placed == [] and "bridge" in h.engine.findings
    out = h.events()
    assert ("CRITICAL", "the PAPER bridge wrote its heartbeat 0s ago — the bridge and the engine must never send"
            " orders together; stop one of them") in out
    assert ("SKIP", "NEW (MOMO_HOD_V2): entries paused by a reconciliation mismatch") in out


@pytest.mark.parametrize(("command", "bridge"), [
    ("/Users/x/backend/.venv/bin/python /Users/x/backend/scripts_local/paper_bridge.py --date 2026-10-13", True),
    ("python3 scripts_local/paper_bridge.py", True),
    ("python paper_bridge.py", True),
    ("/Users/x/.venv/bin/python -m scripts_local.paper_bridge --date 2026-10-13", True),
    ("python -m paper_bridge", True),
    ("/Users/x/.venv/bin/python -m pytest tests/test_paper_bridge.py", False),
    ("/Users/x/.venv/bin/python autotraderctl.py run", False),
    ("tail -f /Users/x/.local/share/autotrader-v1/paper_bridge_state.json", False),
])
def test_the_bridge_process_pattern_finds_the_bridge_and_nothing_else(command: str, bridge: bool) -> None:
    assert bool(re.search(BRIDGE_PROCESS_PATTERN, command)) is bridge


def test_running_bridge_processes_reads_pgrep_and_fails_closed(monkeypatch) -> None:
    calls: list[list[str]] = []

    def pgrep(arguments: list[str], **kwargs: Any) -> Any:
        calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout="4242 python scripts_local/paper_bridge.py --date 2026-10-13\n")

    monkeypatch.setattr(subprocess, "run", pgrep)
    assert running_bridge_processes() == [(4242, "python scripts_local/paper_bridge.py --date 2026-10-13")]
    assert calls == [["/usr/bin/pgrep", "-fl", BRIDGE_PROCESS_PATTERN]]

    monkeypatch.setattr(subprocess, "run", lambda arguments, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    assert running_bridge_processes() == []
    monkeypatch.setattr(subprocess, "run", lambda arguments, **kwargs: SimpleNamespace(returncode=3, stdout=""))
    with pytest.raises(OSError):
        running_bridge_processes()
