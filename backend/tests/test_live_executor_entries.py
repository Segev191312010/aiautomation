"""Execution engine entries: authority, paper checks, the outbox, order ids and every entry gate.

TWS is never contacted: ``tests/fakes/fake_tws.py`` answers through the real
ibapi callbacks. Tests named after a bridge review finding (H1, L6, N1, ...)
port that lesson from scripts_local/paper_bridge.py.
"""
from __future__ import annotations

from contextlib import closing
from datetime import timedelta
from decimal import Decimal
import sqlite3
from typing import Any

import pytest

from tests.fakes.engine_harness import ACCOUNT, Clock, allow_test_strategy, build, ny, promote
from trading.execution.live_executor import ExecutionRefused
from trading.execution.promotion import ExecutionAuthorityError, ExecutionSwitches, revoke_promotion


AAPL, TSLA, NEWX, NEWY = 265598, 76792991, 4931, 4932


def db_rows(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    from config import cfg

    with closing(sqlite3.connect(cfg.DB_PATH)) as connection:
        return connection.execute(sql, params).fetchall()


def skips(h: Any) -> list[str]:
    return [text for kind, text in h.events() if kind == "SKIP"]


# ---- authority ------------------------------------------------------------------------


async def test_the_engine_refuses_to_start_without_a_promotion_record(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, promoted=False, start=False)

    with pytest.raises(ExecutionAuthorityError) as refused:
        await h.engine.start()

    assert refused.value.code == "promotion_missing"
    assert h.tws.placed == [] and "reqIds" not in h.tws.requests  # refused before touching TWS


async def test_a_promotion_of_other_engine_code_authorizes_nothing(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, promoted=False, start=False)
    await promote(code_digest="0" * 64)  # granted for a different build of the order sender

    with pytest.raises(ExecutionAuthorityError, match="promotion_missing"):
        await h.engine.start()


async def test_a_run_through_refuses_with_exit_78_without_authority(tmp_path, monkeypatch) -> None:
    from trading.execution.live_executor import EXIT_REFUSED

    h = await build(tmp_path, monkeypatch, promoted=False, start=False)

    assert await h.engine.run() == EXIT_REFUSED
    assert h.critical()[0].startswith("refusing: promotion_missing")


async def test_shadow_only_strategies_are_skipped_even_with_a_hand_written_promotion(tmp_path, monkeypatch) -> None:
    from trading.execution.promotion import engine_code_digest

    h = await build(tmp_path, monkeypatch)
    # A grant row for ORB_V1 written by hand into the table: the code-owned prefix still refuses it.
    db_rows("SELECT 1")  # the table exists
    from config import cfg

    with closing(sqlite3.connect(cfg.DB_PATH)) as connection:
        connection.execute(
            "INSERT INTO autotrader_exec_promotions (id, action, strategy_name, account, code_digest, actor,"
            " reason, created_at) VALUES (?, 'grant', 'ORB_V1', ?, ?, 'hand', 'hand-written', '2026-10-13')",
            ("f" * 32, ACCOUNT, engine_code_digest()),
        )
        connection.commit()
    await h.decide("MMED", con_id=7001, strategy="ORB_V1")
    await h.engine.tick()

    assert h.tws.placed == []
    assert skips(h) == ["MMED (ORB_V1): shadow-only strategy"]


async def test_the_strategy_comes_from_the_ledger_never_the_run_id(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.seeder.run_for("MOMO_HOD_V2", run_label="orb-lookalike")  # the labels lie on purpose
    await h.seeder.run_for("ORB_V1", run_label="momo_hod-lookalike")
    await h.decide("AAPL", con_id=AAPL)
    await h.decide("MMED", con_id=7001, strategy="ORB_V1")
    await h.engine.tick()

    assert h.placed_ids() == [100, 101, 102]
    out = h.events()
    assert ("SKIP", "MMED (ORB_V1): shadow-only strategy") in out
    assert [text for kind, text in out if kind == "SENT"] == [
        "AAPL (MOMO_HOD_V2): BUY 100 @ 50.00 | stop 49.50 | target 51.00 (orders 100-102)"
    ]


async def test_a_revoked_promotion_stops_new_entries_but_never_exits(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.tick()
    await revoke_promotion(strategy_name="MOMO_HOD_V2", account=ACCOUNT, actor="owner", reason="pause the bot")
    await h.decide("TSLA", con_id=TSLA)
    h.clock.advance(11 * 60)  # the AAPL entry outlives its 10 minute TTL
    await h.decide("NEWX", con_id=NEWX)
    await h.engine.tick()

    assert h.tws.cancelled == [100]  # the exit side (cancelling a stale entry) still runs
    assert h.placed_ids() == [100, 101, 102]
    assert "NEWX (MOMO_HOD_V2): strategy has no active PAPER promotion" in skips(h)


async def test_configuration_can_only_switch_entries_off(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    h.engine.authority.switches = ExecutionSwitches(disabled_strategies=frozenset({"MOMO_HOD_V2"}))
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.tick()
    h.engine.authority.switches = ExecutionSwitches(entries_enabled=False)
    await h.decide("TSLA", con_id=TSLA)
    await h.engine.tick()

    assert h.tws.placed == []
    assert skips(h) == [
        "AAPL (MOMO_HOD_V2): strategy disabled by configuration",
        "TSLA (MOMO_HOD_V2): entries disabled by configuration",
    ]


# ---- paper only, at start and on every reconnect ------------------------------------------


@pytest.mark.parametrize(("accounts", "code"), [
    ("U1234567,DU1234567", "not_paper_account"),
    ("DU7654321", "account_not_managed"),
])
async def test_the_engine_never_starts_unless_every_account_is_paper_and_ours_is_managed(
    tmp_path, monkeypatch, accounts: str, code: str
) -> None:
    h = await build(tmp_path, monkeypatch, start=False, accounts=accounts)

    with pytest.raises(ExecutionRefused) as refused:
        await h.engine.start()

    assert refused.value.code == code
    assert h.tws.placed == []
    assert "U1234567" not in str(refused.value)  # account numbers never reach the owner's phone


async def test_every_reconnect_repeats_the_paper_check_reqids_and_reconciliation(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    assert h.tws.requests.count("reqIds") == 1
    requests_before = h.tws.requests.count("reqAllOpenOrders")

    h.tws.disconnect()
    h.tws.connect()
    await h.engine.handle_reconnect()
    assert h.tws.requests.count("reqIds") == 2
    assert h.tws.requests.count("reqAllOpenOrders") > requests_before
    assert h.engine.started

    h.tws.disconnect()
    h.tws.accounts = "U9999999,DU1234567"  # TWS now also manages a live account
    h.tws.connect()
    with pytest.raises(ExecutionRefused, match="not_paper_account"):
        await h.engine.handle_reconnect()
    assert not h.engine.started
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.intake_decisions()
    assert h.tws.placed == []  # no entry before a clean reconnect


# ---- the outbox ------------------------------------------------------------------------


async def test_the_decision_and_its_bracket_are_persisted_before_any_order_is_sent(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    decision = await h.decide("AAPL", con_id=AAPL)
    seen: list[tuple[Any, ...]] = []
    original = h.tws.placeOrder

    def checking(order_id: int, contract: Any, order: Any) -> None:
        seen.append((
            order_id,
            db_rows("SELECT state FROM autotrader_exec_outbox WHERE decision_id=?", (decision,)),
            db_rows("SELECT state FROM autotrader_exec_brackets"),
            sorted(db_rows("SELECT leg, order_id, order_ref, state FROM autotrader_exec_legs")),
            db_rows("SELECT state, risk_amount FROM autotrader_exec_risk_reservations"),
        ))
        original(order_id, contract, order)

    h.tws.placeOrder = checking  # type: ignore[method-assign]
    await h.engine.tick()

    bracket = h.bracket("AAPL")
    order_id, outbox, brackets, legs, reservations = seen[0]
    assert order_id == 100
    assert (outbox, brackets, reservations) == ([("sending",)], [("sending",)], [("reserved", "50.00")])
    assert legs == [("P", 100, f"at1:{bracket.id}:P", "staged"), ("S", 102, f"at1:{bracket.id}:S", "staged"),
                    ("T", 101, f"at1:{bracket.id}:T", "staged")]
    assert db_rows("SELECT state FROM autotrader_exec_outbox") == [("sent",)]
    assert db_rows("SELECT state FROM autotrader_exec_brackets") == [("working",)]
    assert {row[0] for row in db_rows("SELECT state FROM autotrader_exec_legs")} == {"sent"}
    assert db_rows("SELECT state FROM autotrader_exec_risk_reservations") == [("consumed",)]


async def test_a_decision_is_considered_once_even_across_a_restart(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("AAPL", con_id=AAPL)
    await h.decide("SKIPX", con_id=7002, strategy="ORB_V1")
    for _ in range(3):
        await h.engine.tick()
    restarted = await h.restart()
    for _ in range(2):
        await restarted.engine.tick()

    assert h.placed_ids() == [100, 101, 102]
    assert db_rows("SELECT state, COUNT(*) FROM autotrader_exec_outbox GROUP BY state ORDER BY state") == [
        ("sent", 1), ("skipped", 1)
    ]
    assert [text for kind, text in h.events() if kind == "SKIP"] == ["SKIPX (ORB_V1): shadow-only strategy"]


async def test_order_ids_are_never_below_the_ledger_even_after_tws_resets_its_sequence(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.tick()
    assert h.placed_ids() == [100, 101, 102]

    h.tws.restart_tws(next_valid_id=1)  # a fresh TWS numbers this client's orders from 1 again
    restarted = await h.restart()
    assert restarted.mirror.next_id == 1  # reqIds answered with the reset sequence
    await restarted.decide("TSLA", con_id=TSLA)
    await restarted.engine.tick()

    assert h.placed_ids() == [100, 101, 102, 103, 104, 105]
    assert h.tws.requests.count("reqIds") == 2


async def test_every_leg_is_tagged_and_encoded_as_a_native_bracket(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.tick()

    bracket = h.bracket("AAPL")
    (_, parent), (_, target), (_, stop) = h.tws.placed
    assert [item.orderRef for item in (parent, target, stop)] == [
        f"at1:{bracket.id}:P", f"at1:{bracket.id}:T", f"at1:{bracket.id}:S"
    ]
    assert [(item.action, item.orderType, item.tif, item.transmit, item.parentId) for item in (parent, target, stop)] == [
        ("BUY", "LMT", "DAY", False, 0), ("SELL", "LMT", "GTC", False, 100), ("SELL", "STP", "GTC", True, 100)
    ]
    assert (parent.lmtPrice, target.lmtPrice, stop.auxPrice) == (50.0, 51.0, 49.5)
    assert {item.account for item in (parent, target, stop)} == {ACCOUNT}
    assert not any(item.eTradeOnly or item.firmQuoteOnly for item in (parent, target, stop))


# ---- entry gates (bridge lessons) -----------------------------------------------------------


async def test_l6_decisions_expire_with_their_risk_approval(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    decided = ny(10, 0)
    h.clock.value = decided + timedelta(seconds=20)
    await h.engine.start()
    await h.decide("OLDX", con_id=7003, created=decided, ttl=timedelta(seconds=15))
    await h.decide("BACK", con_id=7004, created=decided - timedelta(seconds=45), ttl=timedelta(seconds=600))
    await h.decide("NEWX", con_id=NEWX, created=decided, ttl=timedelta(seconds=30))
    await h.engine.tick()

    assert h.placed_ids() == [100, 101, 102]
    assert h.bracket("NEWX").state == "working"
    assert skips(h) == [  # oldest decision first
        "BACK (MOMO_HOD_V2): decision expired (65s old)",  # the 60 s backstop beats a long intent
        "OLDX (MOMO_HOD_V2): decision expired (20s old)",
    ]


async def test_l6_an_approval_that_expires_during_the_quote_wait_is_not_sent(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("AAPL", con_id=AAPL, created=h.clock.value - timedelta(seconds=14))
    original = h.tws.reqMktData

    def slow_quote(request_id: int, contract: Any, *args: Any) -> None:
        h.clock.advance(2)
        original(request_id, contract, *args)

    h.tws.reqMktData = slow_quote  # type: ignore[method-assign]
    await h.engine.tick()

    assert h.tws.placed == []
    assert skips(h) == ["AAPL (MOMO_HOD_V2): decision expired (16s old)"]


async def test_l6_an_identical_setup_is_sent_only_once(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    setup = "f" * 64
    await h.decide("AAPL", con_id=AAPL, setup_fingerprint=setup)
    await h.engine.tick()
    h.tws.fill(100, 100, "50.00")
    h.tws.fill(101, 100, "51.00")
    await h.engine.tick()
    assert h.bracket("AAPL").state == "closed"
    first = h.bracket("AAPL").decision_id
    await h.decide("AAPL", con_id=AAPL, setup_fingerprint=setup)
    await h.decide("MSFT", con_id=7005, setup_fingerprint="e" * 64)
    await h.engine.tick()

    assert h.placed_ids()[3:] == [103, 104, 105]  # MSFT only
    assert f"AAPL (MOMO_HOD_V2): setup already sent (decision {first})" in skips(h)


@pytest.mark.parametrize(("held", "reason"), [
    (500, "account already holds 500 — bot only trades symbols it alone holds"),  # the owner's own shares
    (-200, "account already holds -200 — bot only trades symbols it alone holds"),  # the owner is short
])
async def test_n1_entries_only_in_symbols_the_account_does_not_hold(
    tmp_path, monkeypatch, held: int, reason: str
) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    h.tws.positions[AAPL] = Decimal(held)
    await h.engine.start()
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.tick()

    assert h.tws.placed == []
    assert skips(h) == [f"AAPL (MOMO_HOD_V2): {reason}"]


async def test_n1_an_unknown_position_blocks_the_entry(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    h.mirror.positions_done.clear()  # TWS has not reported positions (yet)
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.process_decision((await h.engine.decisions())[0])

    assert h.tws.placed == []
    assert skips(h) == ["AAPL (MOMO_HOD_V2): account position unknown"]


async def test_n6_no_entry_is_sent_after_a_stop_request(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    decision = await h.decide("NEWX", con_id=NEWX)
    original = h.tws.reqMktData

    def quote_then_sigterm(request_id: int, contract: Any, *args: Any) -> None:
        h.engine.stop_event.set()  # SIGTERM lands while the engine waits for the pre-entry quote
        original(request_id, contract, *args)

    h.tws.reqMktData = quote_then_sigterm  # type: ignore[method-assign]
    await h.engine.tick()

    assert h.tws.placed == []
    assert await h.engine.ledger.outbox_row(decision) is None  # the next run evaluates it again
    assert ("INFO", "NEWX: stop requested — entry not sent") in h.events()


@pytest.mark.parametrize(("quantity", "entry", "stop", "target", "reason"), [
    (6000, "50.00", "49.90", "50.50",
     "order notional 300000.00 exceeds 25% of net liquidation 1000000.00"),
    (2000, "50.00", "47.00", "56.00", "order risk 6000.00 exceeds 0.5% of net liquidation 1000000.00"),
])
async def test_m3_oversized_orders_are_refused(
    tmp_path, monkeypatch, quantity: int, entry: str, stop: str, target: str, reason: str
) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("BIG", con_id=7006, quantity=quantity, entry=entry, stop=stop, target=target,
                   quote={"bid": float(entry)})
    await h.engine.tick()

    assert h.tws.placed == []
    assert skips(h) == [f"BIG (MOMO_HOD_V2): {reason}"]


async def test_at_most_three_bot_positions_one_per_symbol(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    for index, symbol in enumerate(("AAA", "BBB", "CCC", "DDD")):
        await h.decide(symbol, con_id=8000 + index)
    await h.decide("AAA", con_id=8000)  # a second setup in a symbol the bot already trades
    await h.engine.tick()

    assert len(h.tws.placed) == 9
    assert skips(h) == [
        "DDD (MOMO_HOD_V2): max 3 positions/entries already active",
        "AAA (MOMO_HOD_V2): symbol already has an active bracket",
    ]


async def test_the_shared_book_spans_every_promoted_strategy(tmp_path, monkeypatch) -> None:
    risky = {"quantity": 5000, "entry": "50.00", "stop": "49.00", "target": "52.00"}  # 5000 risk = 0.5% each
    h = await build(tmp_path, monkeypatch)
    allow_test_strategy(monkeypatch, "MOMO_HOD_V3")
    await promote("MOMO_HOD_V3")
    await h.decide("AAA", con_id=8100, **risky)
    await h.decide("BBB", con_id=8101, strategy="MOMO_HOD_V3", **risky)
    await h.decide("CCC", con_id=8102, **risky)  # 15000 open risk: exactly the 1.5% budget
    await h.decide("DDD", con_id=8103, strategy="MOMO_HOD_V3", quantity=100)
    await h.engine.tick()

    assert len(h.tws.placed) == 9
    assert skips(h) == ["DDD (MOMO_HOD_V3): max 3 positions/entries already active"]
    reservations = await h.engine.ledger.reservations(h.engine.book)
    assert sorted((item.symbol, item.risk_amount, item.state) for item in reservations) == [
        ("AAA", Decimal("5000.00"), "consumed"), ("BBB", Decimal("5000.00"), "consumed"),
        ("CCC", Decimal("5000.00"), "consumed"),
    ]


async def test_one_strategys_realized_loss_spends_the_daily_budget_of_every_strategy(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    allow_test_strategy(monkeypatch, "MOMO_HOD_V3")
    await promote("MOMO_HOD_V3")
    await h.decide("AAA", con_id=8200, quantity=1000, entry="50.00", stop="49.50", target="51.00")
    await h.engine.tick()
    h.tws.fill(100, 1000, "50.00")
    h.tws.fill(102, 1000, "30.00", commission=None)  # a gap through the stop: 20000 lost
    await h.engine.tick()
    assert h.bracket("AAA").realized_pnl == Decimal("-20001.00")  # the entry's 1.00 commission included
    await h.decide("BBB", con_id=8201, strategy="MOMO_HOD_V3")
    await h.engine.tick()

    assert len(h.tws.placed) == 3
    [line] = skips(h)
    # The notifier forwards SKIP lines that mention the daily loss to the owner's phone.
    assert line.startswith("BBB (MOMO_HOD_V3): daily loss budget: realized loss 20001.00")


async def test_paper_kill_switch_blocks_every_new_entry_and_fails_closed(tmp_path, monkeypatch) -> None:
    from trading.storage.runtime_fence import SQLitePaperKillStore

    h = await build(tmp_path, monkeypatch)
    await SQLitePaperKillStore().stop(reason="owner stop")
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.tick()

    async def unreadable() -> bool:
        raise sqlite3.OperationalError("no such table")

    h.engine.kill_switch_active = unreadable
    await h.decide("TSLA", con_id=TSLA)
    await h.engine.tick()

    assert h.tws.placed == []
    assert skips(h) == ["AAPL: kill switch active", "TSLA: kill switch unreadable (OperationalError) — fail closed"]


async def test_the_kill_switch_never_blocks_an_exit(tmp_path, monkeypatch) -> None:
    from trading.storage.runtime_fence import SQLitePaperKillStore

    h = await build(tmp_path, monkeypatch)
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.tick()
    h.tws.fill(100, 100, "50.00")
    await h.decide("WORK", con_id=7400)
    await h.engine.tick()
    await SQLitePaperKillStore().stop(reason="owner stop")
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert 103 in h.tws.cancelled  # the WORK entry is still cancelled at its cutoff
    assert [(order_id, order.totalQuantity) for order_id, order in h.tws.placed[6:]] == [(101, 100)]  # flatten


async def test_a_price_already_at_the_stop_is_not_entered(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("AAPL", con_id=AAPL, quote={"bid": 49.50, "ask": 49.52})
    await h.engine.tick()

    assert h.tws.placed == []
    assert skips(h) == ["AAPL (MOMO_HOD_V2): price 49.5 already at/below stop 49.50"]


@pytest.mark.parametrize(("moment", "reason"), [
    (ny(15, 10), "momo entry window closed"),
    (ny(15, 31), "after 15:30 — no new entries"),
])
async def test_entries_stop_at_the_strategy_cutoff_and_30_minutes_before_the_close(
    tmp_path, monkeypatch, moment: Any, reason: str
) -> None:
    h = await build(tmp_path, monkeypatch, clock=Clock(moment))
    await h.decide("AAPL", con_id=AAPL)
    await h.engine.tick()

    assert h.tws.placed == []
    assert skips(h) == [f"AAPL (MOMO_HOD_V2): {reason}"]


async def test_m4_a_needs_attention_bracket_that_may_hold_shares_blocks_entries_until_flat(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("GHST", con_id=8300)
    await h.engine.tick()
    h.tws.fill(100, 100, "50.00")
    h.tws.fill(101, 100, "51.00")
    h.tws.fill(102, 30, "49.40")  # IBKR filled both exits: the account is short 30
    await h.engine.tick()
    ghost = h.bracket("GHST")
    assert ghost.state == "needs_attention"
    h.clock.advance(121)
    await h.decide("NEWX", con_id=NEWX)
    await h.engine.tick()  # the reconciliation keeps entries paused while the ghost holds shares
    assert len(h.tws.placed) == 3
    assert "NEWX (MOMO_HOD_V2): entries paused by a reconciliation mismatch" in skips(h)

    h.tws.move_position(8300, 30)  # the owner buys the 30 back in TWS: flat again
    h.clock.advance(121)
    await h.engine.tick()
    assert ghost.attention_cleared_at is not None
    await h.decide("NEWY", con_id=NEWY)
    await h.engine.tick()
    assert h.placed_ids()[3:] == [103, 104, 105]


async def test_l4_unmanaged_live_orders_block_their_symbol_and_take_a_slot(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    for order_id, con_id, symbol in ((77, 7077, "SYM77"), (88, 7088, "SYM88")):
        h.tws.add_foreign_order(client_id=18, order_id=order_id, con_id=con_id, symbol=symbol, action="BUY",
                                quantity=10, order_ref="autotrader:old-bridge")
    await h.engine.start()
    assert h.critical() == [
        "order 77 (SYM77) is live at IBKR but not in the execution ledger — SYM77 blocked for new entries; check TWS",
        "order 88 (SYM88) is live at IBKR but not in the execution ledger — SYM88 blocked for new entries; check TWS",
    ]
    assert not h.engine.findings  # outside the at1: scope: blocked, not a mismatch
    await h.decide("AAA", con_id=8400)
    await h.engine.tick()
    await h.decide("NEWX", con_id=NEWX)
    await h.decide("SYM77", con_id=7077)
    await h.engine.tick()

    assert h.placed_ids() == [100, 101, 102]  # AAA + SYM77 + SYM88 fill the three slots
    assert skips(h) == [
        "NEWX (MOMO_HOD_V2): max 3 positions/entries already active",
        "SYM77 (MOMO_HOD_V2): symbol has a live order at IBKR the engine does not manage",
    ]
    h.tws.cancelOrder(88)  # the owner cancels the stray order in TWS (any client may)
    await h.decide("NEWY", con_id=NEWY)
    await h.engine.tick()
    assert h.placed_ids()[3:] == [103, 104, 105]


# ---- owner manual orders: left alone, and they keep bot entries off their symbol --------------


async def queue_manual_order(text: str, *, message_id: int, now: Any) -> str:
    """Queue an owner order exactly as the Telegram assistant does (deterministic parser + validator)."""
    from trading.execution import manual_orders as mo

    parsed = mo.parse_order_text(text)
    assert isinstance(parsed, mo.ParsedOrder), parsed
    con_id = {"AAPL": AAPL, "TSLA": TSLA}[parsed.symbol]
    facts = mo.LiveFacts(
        now=now, accounts_all_paper=True, kill_switch_active=False, bridge_heartbeat_age_s=1.0,
        net_liquidation=Decimal("1000000"),
        quote=mo.Quote(bid=Decimal("189.99"), ask=Decimal("190.01"), last=Decimal("190.00"),
                       observed_at=now - timedelta(seconds=1), real_time=True),
        owner_position_qty=Decimal("0"), open_manual_sell_qty=0, resting_stop_qty=Decimal("0"),
        bot_symbols=frozenset(), manual_open_buy_notional_today=Decimal("0"), recent_manual_orders_10min=0,
    )
    message_date = now - timedelta(seconds=1)
    contract = mo.ContractRef(symbol=parsed.symbol, con_id=con_id, primary_exchange="NASDAQ")
    validated = mo.validate(parsed, facts=facts, contract=contract, message_date=message_date)
    assert isinstance(validated, mo.ValidatedOrder), validated
    return await mo.queue_order(
        validated, chat_id=7, message_id=message_id, update_id=10_000 + message_id, source=parsed.source,
        raw_text=text, message_date=message_date, audit={"test": True}, now=now,
    )


async def test_d3_manual_orders_take_no_bot_slot_and_keep_bot_entries_off_their_symbol(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await queue_manual_order("/buy 10 AAPL 185", message_id=1, now=h.clock.value)
    # The owner's own limit order in TWS (client 0) on another symbol.
    h.tws.add_foreign_order(client_id=0, order_id=0, con_id=TSLA, symbol="TSLA", action="SELL", quantity=5)
    for symbol, con_id in (("AAPL", AAPL), ("TSLA", TSLA), ("NEWX", NEWX)):
        await h.decide(symbol, con_id=con_id)
    await h.engine.tick()

    assert h.placed_ids() == [100, 101, 102]
    assert h.bracket("NEWX").state == "working"  # two owner orders took no bot slot
    assert skips(h) == [
        "AAPL (MOMO_HOD_V2): symbol has a working manual order",
        "TSLA (MOMO_HOD_V2): symbol has a working order at IBKR (owner or another client)",
    ]


async def test_the_engine_never_touches_manual_orders_or_positions_it_did_not_open(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, start=False, clock=Clock(ny(15, 44, 50)))
    # A manual order the bridge sent on the engine's own client id, and the owner's long.
    h.tws.add_foreign_order(client_id=18, order_id=60, con_id=TSLA, symbol="TSLA", action="BUY", quantity=5,
                            order_ref="manual:" + "a" * 32)
    h.tws.positions[AAPL] = Decimal("300")
    await h.engine.start()
    h.clock.value = ny(15, 45, 5)
    for _ in range(3):
        await h.engine.tick()
        h.clock.advance(130)

    assert h.tws.placed == [] and h.tws.cancelled == []
    assert h.critical() == [] and not h.engine.findings
    assert h.engine.unmanaged == {}  # a manual: order is the owner's, not an unmanaged bot order
