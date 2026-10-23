"""No-limit PAPER test L-T1 in the PAPER bridge (bridge v7c-t1; owner plan v2 items 3-5, decisions D7, D8).

The shipped bridge runs with NO_LIMIT_TEST set: no position, entries/day or account-wide caps; the -2 %
daily loss stop and the 3 % day risk budget measured and logged for bot entries, never refusing them
(owner buys keep both, D8); every robot position under a stop that repairs itself; a manual position
without a stop pages once and no longer blocks the robot (D7); six malfunction guards; account values
logged; one t1_snapshot line per decision; a batched 15:45 close-out. The v7c refusal paths stay
covered by tests/test_paper_bridge.py under its ``v7c_limits`` fixture. TWS is never contacted: the
fake broker of test_paper_bridge drives the real IBKR callbacks.
"""
from __future__ import annotations

from dataclasses import MISSING, fields as dataclass_fields, replace
from datetime import date, datetime, time as clock_time, timedelta, timezone
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_paper_bridge import (  # noqa: F401  (bridge_env and v7c_limits are fixtures)
    AAPL_ID,
    ACCOUNT,
    BRIDGE_PATH,
    CLIENT_ID,
    DECIDED,
    MANUAL_T0,
    SESSION,
    STOCK,
    T1_BUDGET_LINE,
    T1_CAPS_LINES,
    T1_MODE_LINE,
    WOULD_HAVE,
    Clock,
    FakeBroker,
    bracket,
    bridge_env,
    connected_broker,
    critical,
    decision_row,
    desk_bridge,
    events,
    execution,
    make_bridge,
    manual_ledger,
    manual_row,
    pb,
    queue_manual,
    stop_leg,
    tws_stop,
    v7c_limits,
    v7c_skip,
    v7c_world,
)

ADVICE_OPEN = "close it with /close OPEN or in TWS, or protect it with a stop that stays working (GTC)"
ADVICE_AAPL = "close it with /close AAPL or in TWS, or protect it with a stop that stays working (GTC)"
OCA_GROUP = "1528474519"  # what TWS reports on a bracket's children (test_paper_bridge's N3 fixture)
SNAPSHOT_KEYS = {
    "mode", "time", "decision_id", "decided_at", "strategy", "symbol", "side", "signal_price", "stop_price",
    "target_price", "size", "equity", "day_pnl", "open_positions", "entries_today", "buying_power",
    "account_values", "budget", "ai", "result", "reason", "orders", "fill", "latency_ms",
}


def snapshots(out: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Every t1_snapshot line, parsed: INFO, one line, valid JSON."""
    found = []
    for kind, text in out:
        if text.startswith(f"{pb.T1_SNAPSHOT} "):
            assert kind == "INFO"
            found.append(json.loads(text.split(" ", 1)[1]))
    return found


def measured_lines(out: list[tuple[str, str]]) -> list[str]:
    return [text for kind, text in out if kind == "INFO"
            and text.startswith((f"{pb.V7C_BUDGET_OK} ", f"{pb.T1_OVER_V7C_LIMITS} "))]


def utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def edited_bridge(tmp_path: Path, old: str, new: str) -> Any:
    """Import a copy of the bridge with one exact edit (the import tripwires run at import)."""
    source = BRIDGE_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, old
    edited = tmp_path / "paper_bridge.py"
    edited.write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("paper_bridge_t1_edited", edited)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


# ---- the switch, the tables, the lines -------------------------------------------------------------


def test_t1_the_mode_is_a_literal_and_the_state_schema_is_unchanged() -> None:
    source = BRIDGE_PATH.read_text(encoding="utf-8")
    assert source.count("\nNO_LIMIT_TEST = True\n") == 1  # a literal in the reviewed file, never configuration
    assert pb.NO_LIMIT_TEST is True and pb.NO_LIMIT_TEST_LABEL == "L-T1"
    for line in source.splitlines():
        if "NO_LIMIT_TEST" in line:
            assert not re.search(r"environ|getenv|argv|toml|config", line), line
    assert pb.BRIDGE_VERSION == "v7c-t1"
    assert pb.STATE_SCHEMA == "paper-bridge-state.v5"  # unchanged: v7c-t1 adds optional Bracket fields only


def test_t1_the_tokens_and_guard_numbers_are_pinned() -> None:
    assert (pb.T1_MODE, pb.T1_OVER_V7C_LIMITS, pb.T1_SNAPSHOT, pb.T1_ACCOUNT_VALUES, pb.T1_LOW_CUSHION,
            pb.T1_REALIZED_LOSS_STEP, pb.T1_BROKER_REJECTED, pb.T1_STOP_REPAIR, pb.T1_UNPROTECTED_MANUAL) == (
        "t1_mode", "t1_over_v7c_limits", "t1_snapshot", "t1_account_values", "t1_low_cushion",
        "t1_realized_loss_step", "t1_broker_rejected", "t1_stop_repair", "t1_unprotected_manual")
    assert pb.T1_GUARD_TOKENS == ("t1_guard_order_rate", "t1_guard_rejections", "t1_guard_symbol_entries",
                                  "t1_guard_duplicate", "t1_guard_outside_hours")
    # Plan v2 defaults (final numbers after Step 0; the import refuses anything below these floors).
    assert (pb.GUARD_ORDERS_PER_STRATEGY, pb.GUARD_ORDER_WINDOW, pb.GUARD_REJECTIONS_TO_HALT,
            pb.GUARD_ENTRIES_PER_SYMBOL, pb.GUARD_DUPLICATE_WINDOW, pb.STOP_REPAIR_DEADLINE) == (
        20, timedelta(seconds=60), 3, 3, timedelta(seconds=5), timedelta(seconds=60))
    assert (pb.GUARD_PAGES_PER_DAY, pb.ENTRY_REJECTION_WINDOW, pb.STOP_REPAIR_CLOSE_DISCOUNT) == (
        2, timedelta(seconds=30), Decimal("0.98"))
    assert (pb.ACCOUNT_VALUES_REQUEST_ID, pb.ACCOUNT_VALUES_REFRESH, pb.LOW_CUSHION) == (
        7004, timedelta(seconds=30), Decimal("0.10"))
    assert pb.ACCOUNT_VALUE_TAGS == ("NetLiquidation", "AvailableFunds", "BuyingPower", "ExcessLiquidity", "Cushion",
                                     "GrossPositionValue")
    assert len({pb.EXEC_REQUEST_ID, pb.SUMMARY_REQUEST_ID, pb.PNL_REQUEST_ID, pb.ACCOUNT_VALUES_REQUEST_ID}) == 4
    # The v7c numbers stay: the per-order ceilings, the -2 % and 3 % measured, the slippage factor.
    assert (pb.MAX_ORDER_NOTIONAL_PCT, pb.MAX_ORDER_RISK_PCT, pb.MAX_DAILY_LOSS_PCT, pb.DAY_RISK_BUDGET_PCT) == (
        Decimal("25"), Decimal("1"), Decimal("2"), Decimal("3"))


@pytest.mark.parametrize(("old", "new"), [
    ('GUARD_ORDERS_PER_STRATEGY = 20 ', 'GUARD_ORDERS_PER_STRATEGY = 19 '),
    ('GUARD_ENTRIES_PER_SYMBOL = 3 ', 'GUARD_ENTRIES_PER_SYMBOL = 2 '),
    ('GUARD_REJECTIONS_TO_HALT = 3 ', 'GUARD_REJECTIONS_TO_HALT = 0 '),
    ('GUARD_DUPLICATE_WINDOW = timedelta(seconds=5)', 'GUARD_DUPLICATE_WINDOW = timedelta(seconds=0)'),
    ('STOP_REPAIR_DEADLINE = timedelta(seconds=60)', 'STOP_REPAIR_DEADLINE = timedelta(seconds=61)'),
])
def test_t1_a_guard_below_the_owners_floor_refuses_the_import(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ValueError, match="below the owner's floor"):
        edited_bridge(tmp_path, old, new)


def test_t1_the_caps_rows_have_no_cap_and_keep_their_last_entry_offsets() -> None:
    assert dict(pb.IDENTITY_CAPS) == {
        "MOMO_HOD_V2": pb.Caps(None, None, timedelta(minutes=60)),
        "ORB_V2": pb.Caps(None, None, timedelta(minutes=30)),
        "VWAP_PB_V2": pb.Caps(None, None, timedelta(minutes=30)),
        "GAP_GO_V2": pb.Caps(None, None, timedelta(minutes=30)),
    }
    assert pb.global_max_open() is None  # no account-wide cap: IBKR's buying power is the real ceiling
    assert pb.caps_lines() == T1_CAPS_LINES and pb.caps_problem() is None
    with pytest.raises(ValueError):
        pb.Caps(0, None, timedelta(minutes=30))  # a cap, when there is one, is still at least 1


@pytest.mark.parametrize(("old", "new", "message"), [
    ('    "ORB_V2": Caps(max_positions=None, max_entries_per_day=None,',
     '    "ORB_V2": Caps(max_positions=1, max_entries_per_day=None,',
     "NO_LIMIT_TEST is on but these rows keep a cap: ORB_V2"),
    ('    "GAP_GO_V2": Caps(max_positions=None, max_entries_per_day=None,',
     '    "GAP_GO_V2": Caps(max_positions=None, max_entries_per_day=1,',
     "NO_LIMIT_TEST is on but these rows keep a cap: GAP_GO_V2"),
    ("\nNO_LIMIT_TEST = True\n", "\nNO_LIMIT_TEST = False\n",
     "NO_LIMIT_TEST is off but these rows have no position cap: GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2"),
    ("\nNO_LIMIT_TEST = True\n", '\nNO_LIMIT_TEST = "yes"\n', "NO_LIMIT_TEST must be a bool literal"),
])
def test_t1_the_import_refuses_a_half_on_build(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        edited_bridge(tmp_path, old, new)


def test_t1_preflight_refuses_a_half_on_table_at_runtime(
    bridge_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_paper_bridge import V7C_CAPS

    monkeypatch.setattr(pb, "IDENTITY_CAPS", V7C_CAPS)  # the switch on, the v7c rows: half on
    assert pb.caps_problem() == ("PREFLIGHT_REFUSED caps: half-on no-limit test (NO_LIMIT_TEST is on but these rows"
                                 " keep a cap: GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2)")
    bridge_env.install(None, Clock(pb.datetime(2026, 10, 13, 10, 0, tzinfo=pb.NY)))
    assert pb.main(["--preflight"]) == pb.EXIT_REFUSED
    assert critical(events(capsys))[-1].startswith("PREFLIGHT_REFUSED caps: half-on no-limit test")


def test_t1_one_budget_text_line_and_a_mode_line_without_it(
    bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.operations import session_check as sc
    from trading.operations.deploy_apply import BUDGET_TEXT

    assert pb.budget_line() == T1_BUDGET_LINE and T1_BUDGET_LINE.startswith(BUDGET_TEXT)
    assert "measured and logged, never blocks bot entries (no-limit test L-T1)" in T1_BUDGET_LINE
    assert sc._BUDGET_RULE.match(T1_BUDGET_LINE) is not None  # the checker's rule 7 still reads pct and factor
    assert pb.mode_line() == T1_MODE_LINE and BUDGET_TEXT not in T1_MODE_LINE
    bridge_env.install(None, Clock(pb.datetime(2026, 10, 13, 10, 0, tzinfo=pb.NY)))
    assert pb.main(["--preflight"]) == pb.EXIT_OK
    lines = [text for _, text in events(capsys)]
    assert [line for line in lines if BUDGET_TEXT in line] == [T1_BUDGET_LINE]  # deploy apply wants exactly one
    assert lines[-2:] == [T1_BUDGET_LINE, T1_MODE_LINE]


# ---- limits measured, never blocking a bot entry -----------------------------------------------------


def test_t1_a_burst_of_ten_decisions_in_one_tick_is_all_sent_and_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names = ["MOMO_HOD_V2"] * 4 + ["ORB_V2"] * 2 + ["VWAP_PB_V2"] * 2 + ["GAP_GO_V2"] * 2
    decisions = tuple(decision_row(f"d{index}", f"SYM{index}", strategy=name, con_id=5000 + index)
                      for index, name in enumerate(names))
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=decisions)
    capsys.readouterr()
    bridge.tick()

    # v7c sent 6 of these at most (MOMO 3, one each for the three; the account-wide 6): L-T1 sends all ten.
    assert len(broker.placed) == 30 and all(bridge.brackets[f"d{index}"].status == "sent" for index in range(10))
    out = events(capsys)
    sent = [index for index, (kind, _) in enumerate(out) if kind == "SENT"]
    measured = [index for index, (kind, text) in enumerate(out) if kind == "INFO"
                and text.startswith((f"{pb.V7C_BUDGET_OK} ", f"{pb.T1_OVER_V7C_LIMITS} "))]
    assert len(sent) == len(measured) == 10 and all(line < send for line, send in zip(measured, sent))
    assert [snapshot["result"] for snapshot in snapshots(out)] == ["sent"] * 10
    assert snapshots(out)[-1]["open_positions"] == {
        "total": 10, "by_strategy": {"GAP_GO_V2": 2, "MOMO_HOD_V2": 4, "ORB_V2": 2, "VWAP_PB_V2": 2}}
    assert not critical(out)


def test_t1_an_entry_over_the_3pct_budget_is_sent_and_measured_before_its_order_leaves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, _ = v7c_world(tmp_path, held=9965, decisions=(decision_row("d1", "AAPL"),))  # open 19,930
    capsys.readouterr()
    before_place: list[str] = []
    original = broker.placeOrder

    def place(order_id: int, contract: Any, sent: Any) -> None:
        if not before_place:  # what bridge.log holds the moment the first order of the bracket leaves
            before_place.append(capsys.readouterr().out)
        original(order_id, contract, sent)

    broker.placeOrder = place
    bridge.tick()

    line = ("t1_over_v7c_limits AAPL (MOMO_HOD_V2): realized 0.00 + open 19930.00 + new 50.00 = 19980.00 (3.00%) vs"
            " 3% of 665000.00 — would refuse: v7c_day_risk_budget (source=bot)")
    assert f"INFO {line}\n" in before_place[0]  # measured before placeOrder, never after
    assert [sent.action for _, sent in broker.placed] == ["BUY", "SELL", "SELL"]
    assert bridge.brackets["d1"].status == "sent"
    [snapshot] = snapshots(events(capsys))
    assert snapshot["budget"] == {"realized_loss": "0.00", "open_risk": "19930.00", "new_risk": "50.00",
                                  "total": "19980.00", "percent": "3.00", "limit_percent": "3",
                                  "would_refuse": ["v7c_day_risk_budget"]}


def test_t1_d8_an_owner_buy_over_3pct_is_refused_while_the_bot_entry_is_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 TSLA stop 185", message_id=1)  # 10 x (190.01 - 185) = 50.10
    item = bracket("open", "OPEN", status="open", parent=30, filled=9965, qty=9965)
    item.stop = "48.00"
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={300: 9965},
                              live_orders={31: "Submitted", 32: "PreSubmitted"},
                              order_objects={32: stop_leg(32, 9965, 48.0, parent=30)})
    broker.reqOpenOrders()
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"open": item}, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.decisions = lambda: [decision_row("d1", "AAPL", created=MANUAL_T0)]
    capsys.readouterr()
    bridge.tick()

    assert bridge.brackets["d1"].status == "sent"  # the bot entry: over 3 %, measured, sent
    numbers = ("v7c_day_risk_budget: realized 0.00 + open 19980.00 + new 50.10 = 20030.10 (3.01%) > 3% of 665000.00"
               " (source=manual)")
    row = manual_row(row_id)  # the owner buy: refused, as in v7c (D8)
    assert (row.status, row.reject_code, row.error) == ("rejected", "v7c_day_risk_budget", f"{numbers}. Nothing sent.")
    assert [sent.orderRef for _, sent in broker.placed] == ["autotrader:MOMO_HOD_V2:d1"] * 3


def test_t1_past_minus_2pct_bot_entries_continue_and_each_whole_percent_pages_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 TSLA stop 185", message_id=1)
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.decisions = lambda: [decision_row("d1", "AAPL", created=MANUAL_T0)]
    broker.pnl(pb.PNL_REQUEST_ID, -13300.0, 0.0, -13300.0)  # the account's realized loss: 2.00 %
    capsys.readouterr()
    bridge.tick()

    assert bridge.brackets["d1"].status == "sent"
    out = events(capsys)
    assert ("INFO", "t1_over_v7c_limits AAPL (MOMO_HOD_V2): realized 13300.00 + open 0.00 + new 50.00 = 13350.00"
            " (2.01%) vs 3% of 665000.00 — would refuse: daily_loss_limit (source=bot)") in out
    limit = ("daily_loss_limit: daily loss limit reached (account realized loss 13300.00 >= 2% of net liquidation"
             " 665000.00)")
    assert (manual_row(row_id).reject_code, manual_row(row_id).error) == (
        "daily_loss_limit", f"{limit} (source=manual). Nothing sent.")  # D8: the owner's buy is still refused
    step = ("t1_realized_loss_step {pct}%: the account's realized loss today is {loss} ({share}% of net liquidation"
            " 665000.00) — bot entries continue (no-limit test L-T1); owner buys are refused past -2%; nothing is"
            " flattened")
    assert critical(out) == [step.format(pct=2, loss="13300.00", share="2.00")]
    for _ in range(3):
        clock.advance(2)
        bridge.tick()
    assert critical(events(capsys)) == []  # once per step

    broker.pnl(pb.PNL_REQUEST_ID, -20000.0, 0.0, -20000.0)  # 3.01 %
    bridge.decisions = lambda: [decision_row("d2", "MSFT", created=clock.value, con_id=4932)]
    clock.advance(2)
    bridge.tick()
    assert bridge.brackets["d2"].status == "sent"
    assert critical(events(capsys)) == [step.format(pct=3, loss="20000.00", share="3.01")]
    assert broker.cancelled == []  # nothing is ever flattened because of a loss


def test_t1_an_over_budget_account_only_logs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, _ = v7c_world(tmp_path, held=10000, clock=clock)  # open 20,000 = 3.01 %
    capsys.readouterr()
    for _ in range(3):
        bridge.tick()
        clock.advance(2)
    out = events(capsys)
    assert not critical(out)
    assert [(kind, text) for kind, text in out if text.startswith("v7c_budget_over")] == [
        ("INFO", "v7c_budget_over: realized 0.00 + open 20000.00 + new 0.00 = 20000.00 (3.01%) > 3% of 665000.00 —"
         " measured only: bot entries continue (no-limit test L-T1), owner buys are refused; never flattened")]
    assert broker.placed == [] and broker.cancelled == []


# ---- stop auto-repair (item 5) and D7 --------------------------------------------------------------


def repair_world(tmp_path: Path, *, stop_status: str = "Cancelled", target_status: str = "Submitted",
                 shown_stop: float | None = None, oca: tuple[str, int] | None = (OCA_GROUP, 3), held: int = 100,
                 clock: Clock | None = None, decisions: tuple[Any, ...] = (), **broker_kwargs: Any
                 ) -> tuple[Any, FakeBroker, Any]:
    """One open MOMO bracket OPEN (orders 30-32, conId 300) holding ``held`` at 50.00, intent stop 49.50 and
    target 51.00; its stop leg 32 has ``stop_status``; TWS reports its target 31 (with the bracket's OCA
    group) and, with ``shown_stop``, the stop price it last showed."""
    item = bracket("open", "OPEN", status="open", parent=30, filled=held, qty=held)
    target = pb.order("SELL", "LMT", held, ACCOUNT, "autotrader:MOMO_HOD_V2:open", lmtPrice=51.0, tif="GTC",
                      parentId=30, transmit=True)
    if oca is not None:
        target.ocaGroup, target.ocaType = oca
    reported = {31: target}
    if shown_stop is not None:
        reported[32] = stop_leg(32, held, shown_stop, parent=30)
    broker = connected_broker(bid=Decimal("50.10"), live_orders={30: "Filled", 31: target_status, 32: stop_status},
                              order_objects=reported, positions={300: held}, **broker_kwargs)
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets={"open": item}, decisions=decisions,
                         clock=clock or Clock(DECIDED + timedelta(seconds=5)))
    return bridge, broker, item


def stops_placed(broker: FakeBroker) -> list[tuple[int, Any]]:
    return [(order_id, sent) for order_id, sent in broker.placed if sent.orderType == "STP"]


@pytest.mark.parametrize(("shown", "price"), [
    (None, 49.5),  # IBKR never showed the stop: the intent
    (49.8, 49.8),  # the owner had raised it in TWS: kept
    (49.0, 49.5),  # it had been widened: back to the intent, never looser
])
def test_t1_a_dead_robot_stop_is_resubmitted_once_in_the_target_oca_group(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], shown: float | None, price: float
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, item = repair_world(tmp_path, shown_stop=shown, clock=clock)
    state = tmp_path / "bridge" / "2026-10-13.json"
    stored: list[dict[str, Any]] = []
    original = broker.placeOrder

    def place(order_id: int, contract: Any, sent: Any) -> None:
        stored.append(json.loads(state.read_text(encoding="utf-8"))["submitted"]["open"])
        original(order_id, contract, sent)

    broker.placeOrder = place
    capsys.readouterr()
    bridge.tick()

    [(stop_id, stop)] = broker.placed
    assert stop_id == 100 and stop.auxPrice == pytest.approx(price)
    assert (stop.action, stop.orderType, stop.totalQuantity, stop.tif, stop.parentId, stop.transmit) == (
        "SELL", "STP", 100, "GTC", 0, True)
    assert (stop.ocaGroup, stop.ocaType, stop.orderRef, stop.account) == (
        OCA_GROUP, 3, "autotrader:MOMO_HOD_V2:open", ACCOUNT)  # never a second unlinked sell next to the target
    assert (stored[0]["stop_id"], stored[0]["stop_repair_from"], stored[0]["stop_repair_outcome"]) == (100, 32, "sent")
    assert (item.stop_id, item.stop_repair_from, item.stop_repair_outcome) == (100, 32, "sent")
    # A later modification (a resize) is encoded like the placement: the resubmitted stop has no parent.
    assert bridge.exit_order(item, "stop", 100, total=100, price=Decimal(str(price))).parentId == 0
    assert bridge.exit_order(item, "target", 31, total=100, price=Decimal("51")).parentId == 30
    shown_price = pb.Decimal(str(price)) if shown is not None and shown == price else Decimal("49.50")
    assert critical(events(capsys)) == [
        f"t1_stop_repair OPEN (MOMO_HOD_V2): stop order 32 is not live (Cancelled) — 100 sh unprotected; stop"
        f" resubmitted once as order 100 at {shown_price} in the target OCA group; closed at market if it is not"
        " working by 10:01:05 ET; entries are refused meanwhile"]

    clock.advance(2)
    bridge.tick()
    assert item.stop_repair_outcome == "repaired" and len(broker.placed) == 1
    out = events(capsys)
    assert [text for _, text in out if text.startswith("t1_stop_repair")] == [
        f"t1_stop_repair OPEN (MOMO_HOD_V2): repaired — stop order 100 works at {Decimal(str(price))} 2s after"
        " detection"]
    assert not critical(out)


def test_t1_a_rejected_resubmitted_stop_closes_the_position_at_market_and_pages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, item = repair_world(tmp_path, clock=clock)
    bridge.tick()  # the one resubmitted stop: order 100
    broker.error(100, 201, "Order rejected - reason: stop price above the market")
    broker.orderStatus(100, "Cancelled", 0, 100, 0)
    clock.advance(2)
    capsys.readouterr()
    bridge.tick()

    assert len(stops_placed(broker)) == 1  # resubmitted at most once
    [(target_id, close)] = [(order_id, sent) for order_id, sent in broker.placed if sent.orderType == "LMT"]
    assert (target_id, close.action, close.totalQuantity, close.parentId, close.ocaGroup) == (31, "SELL", 100, 30,
                                                                                             OCA_GROUP)
    assert close.lmtPrice == pytest.approx(49.09)  # 50.10 x 0.98: a marketable limit on the target's own id
    out = events(capsys)
    assert ("FLATTEN", "OPEN: target repriced to 49.09 for 100 sh (bid 50.1)") in out
    assert critical(out) == [
        "t1_stop_repair OPEN (MOMO_HOD_V2): the resubmitted stop 100 was rejected too (Cancelled, TWS 201 Order"
        " rejected - reason: stop price above the market) — closing at market: target repriced to a marketable"
        " limit; entries stay refused until it is flat"]
    assert clock.value - DECIDED - timedelta(seconds=5) < pb.STOP_REPAIR_DEADLINE  # within 60 s of detection

    for _ in range(5):  # never a second close, never another stop
        clock.advance(2)
        bridge.tick()
    assert len(broker.placed) == 2 and item.stop_repair_outcome == "closing"
    broker.execDetails(-1, None, execution("x1", 31, 100, 49.09))
    broker.orderStatus(31, "Filled", 100, 0, 49.09)
    clock.advance(2)
    bridge.tick()
    assert (item.status, item.exit_reason) == ("closed", "stop_repair")


def test_t1_a_robot_position_still_unprotected_after_60s_is_closed_at_market(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, item = repair_world(tmp_path, clock=clock)
    broker.delay_echoes = True  # IBKR never acknowledges the resubmitted stop
    bridge.tick()
    assert len(stops_placed(broker)) == 1
    for _ in range(29):  # up to 58 s after detection: still waiting
        clock.advance(2)
        bridge.tick()
    assert len(broker.placed) == 1 and broker.cancelled == []
    capsys.readouterr()
    clock.advance(2)  # 60 s
    bridge.tick()

    assert broker.cancelled == [100]  # the resubmitted stop never sells next to the close
    assert [(order_id, sent.orderType) for order_id, sent in broker.placed[1:]] == [(31, "LMT")]
    assert critical(events(capsys)) == [
        "t1_stop_repair OPEN (MOMO_HOD_V2): still unprotected 60s after detection (resubmitted stop 100: no status)"
        " — closing at market: target repriced to a marketable limit; entries stay refused until it is flat"]


def test_t1_with_no_live_exit_leg_the_repair_close_is_one_market_sell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, item = repair_world(tmp_path, target_status="Cancelled", clock=clock)
    bridge.tick()
    [(stop_id, stop)] = broker.placed
    assert (stop_id, stop.ocaGroup, stop.parentId) == (100, "", 0)  # no live target: the stop is the only sell
    broker.error(100, 201, "Order rejected - reason: test")
    broker.orderStatus(100, "Inactive", 0, 100, 0)
    state = tmp_path / "bridge" / "2026-10-13.json"
    stored: list[Any] = []
    original = broker.placeOrder
    broker.placeOrder = lambda order_id, contract, sent: (
        stored.append(json.loads(state.read_text(encoding="utf-8"))["submitted"]["open"]["market_close_id"]),
        original(order_id, contract, sent))
    clock.advance(2)
    bridge.tick()

    [(close_id, close)] = broker.placed[1:]
    assert (close_id, close.action, close.orderType, close.totalQuantity, close.tif, close.parentId) == (
        101, "SELL", "MKT", 100, "DAY", 0)
    assert stored == [101] and item.market_close_id == 101  # stored before it left: never sent twice
    assert bridge.flatten_due(item) is False  # the 15:45 flatten never adds a second sell
    broker.execDetails(-1, None, execution("m1", 101, 100, 49.0))
    broker.orderStatus(101, "Filled", 100, 0, 49.0)
    clock.advance(2)
    bridge.tick()
    assert (item.status, item.exit_reason, item.realized_pnl) == ("closed", "stop_repair", "-100.00")


def test_t1_a_live_target_whose_oca_group_is_unknown_is_closed_never_paired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, item = repair_world(tmp_path, oca=None)
    capsys.readouterr()
    bridge.tick()

    assert stops_placed(broker) == []  # an unlinked stop next to a live target could sell the shares twice
    assert [(order_id, sent.orderType) for order_id, sent in broker.placed] == [(31, "LMT")]
    assert critical(events(capsys)) == [
        "t1_stop_repair OPEN (MOMO_HOD_V2): stop order 32 is not live (Cancelled) and the target's OCA group is"
        " unknown, so no stop can be paired with it — closing at market: target repriced to a marketable limit;"
        " entries stay refused until it is flat"]


def test_t1_a_robot_position_without_a_working_stop_refuses_entries_until_it_is_repaired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, item = repair_world(tmp_path, clock=clock, decisions=(decision_row("d1", "AAPL"),))
    broker.delay_echoes = True  # the resubmitted stop is not acknowledged yet
    bridge.tick()

    why = (f"v7c_unprotected_position: OPEN 100 sh of the MOMO_HOD_V2 bracket, whose stop order 100 is not live —"
           f" every risk-increasing order is refused; {ADVICE_OPEN} (source=bot)")
    assert v7c_skip(bridge, "d1") == why + WOULD_HAVE  # the robot's own: the strict rule stays (D7)
    assert [order_id for order_id, _ in broker.placed] == [100]
    broker.deliver_echoes()
    clock.advance(2)
    bridge.decisions = lambda: [decision_row("d2", "MSFT", created=clock.value, con_id=4932)]
    bridge.tick()
    assert bridge.brackets["d2"].status == "sent"  # repaired: entries resume


def test_t1_a_robot_stop_that_dies_during_the_quote_wait_refuses_the_entry(tmp_path: Path) -> None:
    bridge, broker, _ = v7c_world(tmp_path, held=100, decisions=(decision_row("d1", "AAPL"),))
    original = broker.reqMktData

    def stop_dies_during_the_wait(request_id: int, contract: Any, *args: Any) -> None:
        broker.orderStatus(32, "Cancelled", 0, 100, 0)
        original(request_id, contract, *args)

    broker.reqMktData = stop_dies_during_the_wait
    bridge.tick()

    assert broker.placed == []  # the protect check runs after the quote wait
    assert v7c_skip(bridge, "d1").startswith("v7c_unprotected_position: OPEN 100 sh of the MOMO_HOD_V2 bracket,"
                                             " whose stop order 32 is not live")


def test_t1_an_approval_that_expires_while_its_bid_is_withheld_is_skipped_with_no_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(bid=None)
    bridge = make_bridge(tmp_path, broker, clock=clock, decisions=(decision_row("d1", "AAPL"),))

    def withheld(request_id: int, contract: Any, *args: Any) -> None:
        clock.advance(20)  # no bid ever comes; the wait outlives the 15 s approval

    broker.reqMktData = withheld
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []
    assert v7c_skip(bridge, "d1") == "decision expired (25s old)"
    [snapshot] = snapshots(events(capsys))
    assert (snapshot["result"], snapshot["reason"], snapshot["orders"]) == ("skipped", "decision expired (25s old)",
                                                                             None)


def test_t1_the_protect_check_runs_last_after_the_quote_wait_and_the_fences(tmp_path: Path) -> None:
    calls: list[str] = []
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL"),))
    quote, place, check = broker.reqMktData, broker.placeOrder, bridge.budget_check
    broker.reqMktData = lambda request_id, contract, *args: (calls.append("quote"), quote(request_id, contract, *args))
    broker.placeOrder = lambda order_id, contract, sent: (calls.append("place"), place(order_id, contract, sent))
    bridge.kill_switch_active = lambda: calls.append("kill switch") is None and False
    bridge.identity_fence_active = lambda identity: calls.append("identity fence") is None and False
    bridge.budget_check = lambda risk, **kwargs: (calls.append("protect"), check(risk, **kwargs))[1]
    bridge.tick()

    assert calls == ["quote", "kill switch", "identity fence", "protect", "place", "place", "place"]
    assert bridge.brackets["d1"].status == "sent"


def test_t1_a_restart_never_resubmits_the_stop_and_closes_at_the_deadline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    item = replace(bracket("open", "OPEN", status="open", parent=30, filled=100), stop_id=100, stop_repair_from=32,
                   stop_repair_outcome="sent", stop_repair_detected=utc(clock.value - timedelta(seconds=50)))
    target = pb.order("SELL", "LMT", 100, ACCOUNT, "autotrader:MOMO_HOD_V2:open", lmtPrice=51.0, tif="GTC",
                      parentId=30, transmit=True)
    target.ocaGroup, target.ocaType = OCA_GROUP, 3
    broker = connected_broker(bid=Decimal("50.10"), live_orders={31: "Submitted"}, order_objects={31: target},
                              positions={300: 100})
    broker.reqOpenOrders()  # the resubmitted stop 100 never reached IBKR before the restart
    bridge = make_bridge(tmp_path, broker, brackets={"open": item}, clock=clock)
    for _ in range(4):  # 50 s .. 56 s after detection
        bridge.tick()
        clock.advance(2)
    assert broker.placed == []  # at most once: never resubmitted after a restart
    clock.advance(2)  # 60 s
    bridge.tick()
    assert [(order_id, sent.orderType) for order_id, sent in broker.placed] == [(31, "LMT")]


def test_t1_d7_a_manual_position_without_a_stop_pages_once_a_day_and_the_robot_keeps_trading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 TSLA stop 185", message_id=1)
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)  # 100 AAPL held (the owner's), no stop anywhere
    bridge.decisions = lambda: [decision_row("d1", "MSFT", created=clock.value, con_id=4932)]
    capsys.readouterr()
    bridge.tick()

    assert bridge.brackets["d1"].status == "sent"  # D7: the robot keeps trading
    out = events(capsys)
    assert ("INFO", "t1_over_v7c_limits MSFT (MOMO_HOD_V2): realized 0.00 + open 0.00 + new 50.00 = 50.00 (0.01%)"
            " vs 3% of 665000.00 — would refuse: v7c_unprotected_position (source=bot)") in out
    why = (f"v7c_unprotected_position: AAPL 100 sh held, working stops cover 0 — every risk-increasing order is"
           f" refused; {ADVICE_AAPL}")
    assert (manual_row(row_id).reject_code, manual_row(row_id).error) == (
        "v7c_unprotected_position", f"{why} (source=manual). Nothing sent.")  # owner buys stay refused
    assert not critical(out)  # a position update may still be on its way: the page waits 15 s

    page = (f"t1_unprotected_manual AAPL: 100 sh held, working stops cover 0 — owner buys are refused until it has a"
            f" working stop; the robot keeps trading (no-limit test L-T1, D7); {ADVICE_AAPL}")
    for _ in range(10):
        clock.advance(2)
        bridge.tick()
    assert critical(events(capsys)) == [page]
    broker.book.append((0, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), tws_stop(0, 100), "PreSubmitted", 100.0))
    clock.advance(31)
    bridge.tick()
    assert not bridge.account_open_risk().unprotected
    broker.book.clear()  # cancelled again later in the day: a new episode, but once per symbol per day
    for _ in range(25):
        clock.advance(2)
        bridge.tick()
    assert critical(events(capsys)) == []


def test_t1_an_unreconciled_robot_position_without_a_stop_still_blocks_and_is_never_repaired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    item = bracket("att", "ATT", status="needs_attention", parent=30, filled=100,
                   attention_reason="not found at IBKR at startup")
    broker = connected_broker(bid=Decimal("50.10"), positions={300: 100})
    bridge = make_bridge(tmp_path, broker, brackets={"att": item}, clock=clock,
                         decisions=(decision_row("d1", "AAPL"),))
    capsys.readouterr()
    bridge.tick()
    assert broker.placed == []  # IBKR could not confirm it: the owner acts, the bridge sends nothing
    assert v7c_skip(bridge, "d1").startswith("v7c_unprotected_position: ATT 100 sh of the MOMO_HOD_V2 bracket")
    for _ in range(10):
        clock.advance(2)
        bridge.tick()
    assert critical(events(capsys)) == [
        "v7c_unprotected_position ATT: 100 sh of the MOMO_HOD_V2 bracket, whose stop order 32 is not live — every"
        " risk-increasing order (bot and manual) is refused; close it with /close ATT or in TWS, or protect it with"
        " a stop that stays working (GTC)"]


# ---- the malfunction guards (item 4) ----------------------------------------------------------------


def sent_bracket(decision_id: str, symbol: str, *, parent: int, at: datetime, status: str = "cancelled",
                 strategy: str = "MOMO_HOD_V2", qty: int = 100) -> Any:
    item = bracket(decision_id, symbol, status=status, parent=parent, qty=qty)
    return replace(item, strategy_name=strategy, sent_at=utc(at))


def test_t1_guard_order_rate_refuses_the_21st_entry_of_an_identity_in_60s(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    earlier = {f"e{index}": sent_bracket(f"e{index}", f"E{index:02d}", parent=1000 + 3 * index, at=DECIDED)
               for index in range(19)}  # 19 MOMO entries in the last 5 s
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, brackets=earlier, clock=clock, decisions=(
        decision_row("m20", "M20", con_id=6020), decision_row("m21", "M21", con_id=6021),
        decision_row("m22", "M22", con_id=6022), decision_row("m23", "M23", con_id=6023),
        decision_row("o1", "O1", strategy="ORB_V2", con_id=6030)))
    capsys.readouterr()
    bridge.tick()

    assert bridge.brackets["m20"].status == "sent"  # the 20th in 60 s: just under
    refusal = "t1_guard_order_rate: MOMO_HOD_V2 sent 20 entries in the last 60s (limit 20) — refused"
    assert [v7c_skip(bridge, name) for name in ("m21", "m22", "m23")] == [refusal] * 3
    assert bridge.brackets["o1"].status == "sent"  # another identity keeps trading
    out = events(capsys)
    assert ("SKIP", f"M21 (MOMO_HOD_V2): {refusal}") in out
    assert critical(out) == [  # its first two trips of the day page; later ones are SKIP lines only
        "t1_guard_order_rate M21 (MOMO_HOD_V2): MOMO_HOD_V2 sent 20 entries in the last 60s (limit 20) — refused",
        "t1_guard_order_rate M22 (MOMO_HOD_V2): MOMO_HOD_V2 sent 20 entries in the last 60s (limit 20) — refused"
        " (trip 2 today: an abort rule — deploy undo tonight)"]

    clock.advance(56)  # the 19 are older than 60 s; only m20 is recent
    bridge.decisions = lambda: [decision_row("m24", "M24", created=clock.value, con_id=6024)]
    bridge.tick()
    assert bridge.brackets["m24"].status == "sent"


def test_t1_guard_entries_per_symbol_refuses_a_fourth_entry_in_a_symbol(tmp_path: Path) -> None:
    clock = Clock(DECIDED + timedelta(minutes=10))
    earlier = {f"a{index}": sent_bracket(f"a{index}", "AAPL", parent=1000 + 3 * index, status="closed",
                                         at=DECIDED + timedelta(minutes=index)) for index in range(3)}
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, brackets=earlier, clock=clock,
                         decisions=(decision_row("a3", "AAPL", created=clock.value),))
    bridge.tick()
    assert v7c_skip(bridge, "a3") == "t1_guard_symbol_entries: AAPL was entered 3 times today (limit 3) — refused"

    del earlier["a2"]  # two entries before: the third is sent
    bridge = make_bridge(tmp_path / "under", connected_broker(bid=Decimal("50.10")), brackets=earlier, clock=clock,
                         decisions=(decision_row("a3", "AAPL", created=clock.value),))
    bridge.tick()
    assert bridge.brackets["a3"].status == "sent"


@pytest.mark.parametrize(("age", "quantity", "duplicate"), [(3, 100, True), (4.9, 100, True), (5, 100, False),
                                                            (3, 99, False)])
def test_t1_guard_duplicate_refuses_the_same_symbol_side_and_size_within_5s(
    tmp_path: Path, age: float, quantity: int, duplicate: bool
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=30))
    rejected = sent_bracket("r", "AAPL", parent=1000, at=clock.value - timedelta(seconds=age))  # e.g. a 201
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, brackets={"r": rejected}, clock=clock,
                         decisions=(decision_row("d", "AAPL", created=clock.value, quantity=quantity),))
    bridge.tick()
    if duplicate:
        assert broker.placed == []
        assert v7c_skip(bridge, "d") == (f"t1_guard_duplicate: BUY 100 AAPL repeats order 1000 sent {age:.1f}s ago"
                                         " (window 5s) — refused")
    else:
        assert bridge.brackets["d"].status == "sent"


def test_t1_guard_outside_hours_refuses_an_entry_before_the_open_and_after_the_close(tmp_path: Path) -> None:
    clock = Clock(pb.datetime(2026, 10, 13, 9, 29, 59, tzinfo=pb.NY))
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, clock=clock,
                         decisions=(decision_row("pre", "PRE", created=clock.value - timedelta(seconds=2)),))
    bridge.tick()
    assert broker.placed == []
    assert v7c_skip(bridge, "pre") == ("t1_guard_outside_hours: 09:29:59 ET is outside the XNYS session 09:30-16:00 ET"
                                       " — refused")
    clock.value = pb.datetime(2026, 10, 13, 9, 30, 0, tzinfo=pb.NY)
    bridge.decisions = lambda: [decision_row("open", "OPN", created=clock.value - timedelta(seconds=2))]
    bridge.tick()
    assert bridge.brackets["open"].status == "sent"
    # After the close the last-entry deadlines refuse first; the guard holds on its own as well.
    probe = pb.Bracket(decision_id="p", symbol="P", con_id=1, primary_exchange="", strategy="momo",
                       strategy_name="MOMO_HOD_V2", qty=1, entry="1", stop="0.5", target="2")
    for moment, refused in ((pb.datetime(2026, 10, 13, 15, 59, 59, tzinfo=pb.NY), False),
                            (pb.datetime(2026, 10, 13, 16, 0, 0, tzinfo=pb.NY), True)):
        clock.value = moment
        found = bridge.guard_problem(probe)
        assert (found is not None and found[0] == pb.T1_GUARD_OUTSIDE_HOURS) is refused


def reject_entry(broker: FakeBroker, parent: int, message: str = "Order rejected - reason: insufficient buying"
                 " power") -> None:
    """IBKR rejects a bracket: error 201 on the parent, the parent Inactive, the children cancelled."""
    broker.error(parent, 201, message)
    broker.orderStatus(parent, "Inactive", 0, 100, 0)
    for child in (parent + 1, parent + 2):
        broker.orderStatus(child, "Cancelled", 0, 100, 0)


def test_t1_a_201_rejection_is_never_retried_pages_once_per_identity_a_day_and_the_next_signal_goes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, clock=clock, decisions=(decision_row("d1", "AAA", con_id=7001),))
    bridge.tick()
    capsys.readouterr()
    reject_entry(broker, 100)
    clock.advance(2)
    bridge.decisions = lambda: [decision_row("d1", "AAA", con_id=7001),
                                decision_row("d2", "BBB", created=clock.value, con_id=7002)]
    bridge.tick()

    d1 = bridge.brackets["d1"]
    assert (d1.status, d1.broker_rejection) == ("cancelled", "201 Order rejected - reason: insufficient buying power")
    assert bridge.brackets["d2"].status == "sent"  # the next signal carries on
    out = events(capsys)
    assert ("INFO", "TWS 100 201 Order rejected - reason: insufficient buying power (entry order: the bridge pages"
            " it once)") in out
    assert not [text for kind, text in out if kind == "CANCELLED"]  # a rejection reaches the phone once
    assert critical(out) == [
        "t1_broker_rejected AAA (MOMO_HOD_V2): IBKR rejected the entry (TWS 201 Order rejected - reason: insufficient"
        " buying power) — not sent again; 1 in a row for MOMO_HOD_V2 (halts at 3); later rejections today are"
        " logged, not paged"]

    reject_entry(broker, 103)  # d2 too: logged, not paged
    for _ in range(5):
        clock.advance(2)
        bridge.tick()
    out = events(capsys)
    assert not critical(out)
    assert ("INFO", "t1_broker_rejected BBB (MOMO_HOD_V2): IBKR rejected the entry (TWS 201 Order rejected - reason:"
            " insufficient buying power) — not sent again; 2 in a row for MOMO_HOD_V2 (halts at 3)") in out
    assert [order_id for order_id, sent in broker.placed if sent.action == "BUY"] == [100, 103]  # never resent


def test_t1_three_broker_rejections_in_a_row_halt_the_identity_for_the_day(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, clock=clock)
    capsys.readouterr()
    for index in range(3):
        bridge.decisions = lambda index=index: [decision_row(f"r{index}", f"R{index}", created=clock.value,
                                                             con_id=7100 + index)]
        bridge.tick()
        reject_entry(broker, bridge.brackets[f"r{index}"].parent)
        clock.advance(2)
        bridge.decisions = lambda: []
        bridge.tick()
    assert bridge.halted == {"MOMO_HOD_V2": "3 broker rejections in a row, the last TWS 201 Order rejected - reason:"
                                            " insufficient buying power"}
    bridge.decisions = lambda: [decision_row("r3", "R3", created=clock.value, con_id=7103),
                                decision_row("o1", "O1", strategy="ORB_V2", created=clock.value, con_id=7110)]
    bridge.tick()

    assert v7c_skip(bridge, "r3") == ("t1_guard_rejections: MOMO_HOD_V2 is halted for the day (3 broker rejections in"
                                      " a row, the last TWS 201 Order rejected - reason: insufficient buying power)")
    assert bridge.brackets["o1"].status == "sent"  # other identities keep trading
    pages = critical(events(capsys))
    assert len(pages) == 2 and pages[0].startswith("t1_broker_rejected R0 (MOMO_HOD_V2)")
    assert pages[1] == ("t1_guard_rejections MOMO_HOD_V2: 3 broker rejections in a row, the last TWS 201 Order"
                        " rejected - reason: insufficient buying power — no new MOMO_HOD_V2 entries for the rest of"
                        " 2026-10-13; other identities keep trading; its open brackets are still managed")

    # A restart keeps the halt: it is recounted from the day's own state (no fence row is written).
    restarted = make_bridge(tmp_path, connected_broker(bid=Decimal("50.10")), clock=clock,
                            brackets=pb.StateStore(state_dir=tmp_path / "bridge", legacy_path=None,
                                                   session_date=SESSION).load())
    restarted.recount_rejections()
    assert "MOMO_HOD_V2" in restarted.halted and "ORB_V2" not in restarted.halted


def test_t1_two_rejections_then_an_accepted_entry_reset_the_streak(tmp_path: Path) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, clock=clock)
    for index, outcome in enumerate(("reject", "reject", "fill", "reject")):
        bridge.decisions = lambda index=index: [decision_row(f"r{index}", f"R{index}", created=clock.value,
                                                             con_id=7200 + index)]
        bridge.tick()
        parent = bridge.brackets[f"r{index}"].parent
        if outcome == "reject":
            reject_entry(broker, parent)
        else:
            broker.execDetails(-1, None, execution(f"f{index}", parent, 100, 50.0))
            broker.orderStatus(parent, "Filled", 100, 0, 50.0)
        clock.advance(2)
        bridge.decisions = lambda: []
        bridge.tick()
    assert bridge.rejection_streak["MOMO_HOD_V2"] == 1 and bridge.halted == {}


# ---- account values, the snapshot ------------------------------------------------------------------


def test_t1_account_values_are_logged_never_gate_and_never_change_the_net_liquidation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(bid=Decimal("50.10"))
    broker.account_values_reply = {"NetLiquidation": "1.00", "AvailableFunds": "-5000.00", "BuyingPower": "0",
                                   "ExcessLiquidity": "3000.00", "Cushion": "0.0451", "GrossPositionValue": "2600000.00"}
    bridge = make_bridge(tmp_path, broker, clock=clock, decisions=(decision_row("d1", "AAPL"),))
    capsys.readouterr()
    bridge.tick()

    assert bridge.net_liq == Decimal("665000.00")  # the startup NetLiquidation, never request 7004's
    assert bridge.brackets["d1"].status == "sent"  # no buying power, a 4.5 % cushion: logged, never gating
    out = events(capsys)
    assert ("INFO", "t1_account_values NetLiquidation=1.00 AvailableFunds=-5000.00 BuyingPower=0"
            " ExcessLiquidity=3000.00 Cushion=0.0451 GrossPositionValue=2600000.00") in out
    assert critical(out) == ["t1_low_cushion: cushion 4.5% < 10% (excess liquidity 3000.00, net liquidation 1.00) —"
                             " IBKR may liquidate positions; entries are not blocked by it (logged only)"]
    [snapshot] = snapshots(out)
    assert (snapshot["buying_power"], snapshot["equity"], snapshot["account_values"]["NetLiquidation"]) == (
        "0", "665000.00", "1.00")
    tags = "NetLiquidation,AvailableFunds,BuyingPower,ExcessLiquidity,Cushion,GrossPositionValue"
    assert broker.summary_requests == [(1, "NetLiquidation"), (7004, tags)]
    clock.advance(30)
    bridge.tick()
    assert broker.summary_cancels == [7004] and broker.summary_requests[-1] == (7004, tags)  # re-read every 30 s
    assert not critical(events(capsys))  # the same cushion step pages once


def test_t1_every_processed_decision_logs_a_snapshot_with_every_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    intent = {"quantity": 100, "entry_limit": "50.00", "stop_price": "49.50", "target_price": "51.00"}
    review = {"decision": "PROPOSE", "rationale": "Breakout above the high of day on 3x volume.", "confidence": "0.72"}
    reviewed = replace(decision_row("d1", "AAPL"), payload_json=json.dumps({"risk": {"intent": intent},
                                                                            "ai_review": review}))
    decisions = (reviewed, decision_row("shadow", "SHDW", strategy="ORB_V1", con_id=4940),
                 decision_row("old", "OLD", created=DECIDED - timedelta(minutes=1), con_id=4941),
                 decision_row("again", "AAPL", con_id=4931))
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=decisions)
    capsys.readouterr()
    bridge.tick()

    found = {snapshot["decision_id"]: snapshot for snapshot in snapshots(events(capsys))}
    assert set(found) == {"d1", "shadow", "old", "again"}
    assert all(set(snapshot) == SNAPSHOT_KEYS for snapshot in found.values())  # every field on every signal
    sent = found["d1"]
    assert {key: sent[key] for key in ("mode", "strategy", "symbol", "side", "signal_price", "stop_price",
                                       "target_price", "size", "equity", "buying_power", "result", "reason",
                                       "orders", "latency_ms", "fill")} == {
        "mode": "L-T1", "strategy": "MOMO_HOD_V2", "symbol": "AAPL", "side": "BUY", "signal_price": "50.00",
        "stop_price": "49.50", "target_price": "51.00", "size": 100, "equity": "665000.00",
        "buying_power": "2400000.00", "result": "sent", "reason": None, "orders": [100, 101, 102],
        "latency_ms": 5000, "fill": {"filled": 0, "avg_entry": "0"}}
    assert sent["ai"] == {"verdict": "PROPOSE", "reason": "Breakout above the high of day on 3x volume.",
                          "confidence": "0.72"}
    assert sent["day_pnl"] == {"bot_realized": "0.00", "account_realized_loss": "0.00"}
    assert sent["open_positions"] == {"total": 1, "by_strategy": {"MOMO_HOD_V2": 1}}
    assert sent["entries_today"] == {"strategy": 1, "symbol": 1}
    assert sent["budget"]["would_refuse"] == [] and sent["time"].startswith("2026-10-13T10:00:05")
    assert (found["shadow"]["result"], found["shadow"]["reason"], found["shadow"]["budget"]) == (
        "skipped", pb.SHADOW_ONLY, None)
    assert found["shadow"]["ai"] == {"verdict": None, "reason": None, "confidence": None}  # the decision id joins
    assert found["old"]["reason"] == "decision expired (65s old)"
    assert found["again"]["reason"] == "symbol already has an active bracket"  # one bracket per symbol


# ---- the batched 15:45 close-out, half days, the time base -----------------------------------------


class Bids(FakeBroker):
    """A bid for every contract but ``silent`` ones, which never answer."""

    def __init__(self, *args: Any, silent: set[int], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.silent = silent
        self.quote_requests = 0

    def reqMktData(self, request_id: int, contract: Any, *args: Any) -> None:
        self.quote_requests += 1
        if contract.conId not in self.silent:
            self.tickPrice(request_id, 1, 50.20, None)


def test_t1_the_close_out_reprices_26_brackets_together_once_each_and_is_flat_by_1548(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(pb.datetime(2026, 10, 13, 15, 45, 0, tzinfo=pb.NY))
    items = {f"b{index}": bracket(f"b{index}", f"S{index:02d}", status="open", parent=1000 + 3 * index, filled=100)
             for index in range(26)}
    live = {}
    for item in items.values():
        live.update({item.target_id: "Submitted", item.stop_id: "PreSubmitted"})
    silent = {items[f"b{index}"].con_id for index in range(5)}  # five with no live bid: one shared wait
    broker = Bids(silent=silent, live_orders=live, positions={item.con_id: 100 for item in items.values()})
    broker.connect("127.0.0.1", 7497, CLIENT_ID)
    broker.reqAccountSummary(1, "All", "NetLiquidation")
    broker.reqPositions()
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets=items, clock=clock)
    bridge.quote_timeout = pb.QUOTE_TIMEOUT_S
    bridge.fallback_quote = lambda item: pb.SnapshotQuote(price=Decimal("50.00"), observed_at="2026-10-13T19:44:00+00:00")
    waited = {"seconds": 0.0}

    def sleep(seconds: float) -> None:  # every quote wait moves the session clock
        waited["seconds"] += seconds
        clock.advance(seconds)

    monkeypatch.setattr(pb, "time", SimpleNamespace(monotonic=lambda: clock.value.timestamp(), sleep=sleep))
    state = tmp_path / "bridge" / "2026-10-13.json"
    attempted: list[int] = []
    original = broker.placeOrder

    def place(order_id: int, contract: Any, sent: Any) -> None:
        if not attempted:  # every attempt is stored before the first reprice leaves
            stored = json.loads(state.read_text(encoding="utf-8"))["submitted"]
            attempted.append(sum(1 for entry in stored.values() if entry["flatten_attempted"]))
        original(order_id, contract, sent)

    broker.placeOrder = place
    capsys.readouterr()
    bridge.close_out()

    assert attempted == [26]
    assert sorted(order_id for order_id, _ in broker.placed) == sorted(item.target_id for item in items.values())
    assert broker.quote_requests == 26 and waited["seconds"] <= pb.QUOTE_TIMEOUT_S + 0.1  # one wait, not five
    assert clock.value < pb.datetime(2026, 10, 13, 15, 45, 5, tzinfo=pb.NY)  # every reprice has left
    assert len([text for kind, text in events(capsys) if kind == "FLATTEN"]) == 26
    for item in items.values():  # the marketable targets fill; IBKR's OCA cancels each stop
        broker.execDetails(-1, None, execution(f"x{item.parent}", item.target_id, 100, 49.9))
        broker.orderStatus(item.target_id, "Filled", 100, 0, 49.9)
        broker.orderStatus(item.stop_id, "Cancelled", 0, 100, 0)
        broker.position(ACCOUNT, SimpleNamespace(conId=item.con_id), 0.0, 0.0)
    clock.value = pb.datetime(2026, 10, 13, 15, 48, 0, tzinfo=pb.NY)
    monkeypatch.setattr(pb, "time", time)
    bridge.tick()
    assert bridge.positions_not_flat() == [] and len(broker.placed) == 26  # flat by 15:48, one reprice each
    clock.value = pb.datetime(2026, 10, 13, 15, 50, 0, tzinfo=pb.NY)
    bridge.tick()
    assert ("FLAT", "no open engine positions") in events(capsys)


def test_t1_a_half_day_trades_normally_entries_stop_1230_close_out_1245_flat_before_1300(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    day = date(2026, 11, 27)  # 13:00 close (D6 changed: trade half days normally)

    def at(hour: int, minute: int, second: int = 0) -> datetime:
        return datetime(2026, 11, 27, hour, minute, second, tzinfo=pb.NY)

    clock = Clock(at(12, 29, 50))
    broker = connected_broker(bid=Decimal("50.20"))
    bridge = make_bridge(tmp_path, broker, clock=clock, session_date=day,
                         decisions=(decision_row("early", "EARLY", strategy="ORB_V2", created=at(12, 29, 45)),))
    bridge.tick()
    early = bridge.brackets["early"]
    assert early.status == "sent"
    clock.value = at(12, 30, 0)
    bridge.decisions = lambda: [decision_row("late", "LATE", strategy="ORB_V2", created=at(12, 29, 58), con_id=4999)]
    bridge.tick()
    assert v7c_skip(bridge, "late") == "after 12:30 — no new entries"
    broker.execDetails(-1, None, execution("e1", early.parent, 100, 50.0))
    broker.position(ACCOUNT, SimpleNamespace(conId=early.con_id), 100.0, 50.0)
    clock.value = at(12, 40, 0)
    bridge.tick()
    assert early.status == "open"
    capsys.readouterr()
    clock.value = at(12, 45, 0)
    bridge.tick()
    out = events(capsys)
    assert ("INFO", "12:45 ET: flattening all engine positions") in out
    assert [(order_id, sent.orderType) for order_id, sent in broker.placed[3:]] == [(early.target_id, "LMT")]
    broker.execDetails(-1, None, execution("x1", early.target_id, 100, 49.94))
    broker.position(ACCOUNT, SimpleNamespace(conId=early.con_id), 0.0, 0.0)
    clock.value = at(12, 47, 0)
    bridge.tick()
    assert early.status == "closed" and bridge.positions_not_flat() == []  # flat before 13:00
    probe = replace(early, decision_id="probe")
    clock.value = at(13, 0, 0)
    assert bridge.guard_problem(probe)[0] == pb.T1_GUARD_OUTSIDE_HOURS


@pytest.mark.parametrize(("day", "offset"), [(date(2026, 10, 27), 6), (date(2026, 11, 3), 7)])
def test_t1_the_session_runs_on_the_same_et_times_at_a_6_and_a_7_hour_local_offset(
    tmp_path: Path, day: date, offset: int
) -> None:
    """Israel is ET+6 from 25 Oct to 1 Nov 2026 and ET+7 from 2 Nov: the bridge's checkpoints are ET times."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Jerusalem"
    time.tzset()
    try:
        times = pb.session_times(day)
        names = ("opens_at", "last_entry", "flatten", "flatten_alert", "verify", "stop", "final_alert", "closes_at")
        assert [getattr(times, name).astimezone(pb.NY).strftime("%H:%M") for name in names] == [
            "09:30", "15:30", "15:45", "15:46", "15:50", "15:55", "15:58", "16:00"]
        local = times.flatten.astimezone()  # the Mac's own clock
        assert local.utcoffset() - times.flatten.utcoffset() == timedelta(hours=offset)
        assert local.strftime("%H:%M") == f"{15 + offset}:45"
        clock = Clock(datetime.combine(day, clock_time(9, 29, 59), tzinfo=pb.NY))
        broker = connected_broker(bid=Decimal("50.10"))
        bridge = make_bridge(tmp_path, broker, clock=clock, session_date=day,
                             decisions=(decision_row("pre", "PRE", created=clock.value - timedelta(seconds=2)),))
        bridge.tick()
        assert v7c_skip(bridge, "pre").startswith("t1_guard_outside_hours: 09:29:59 ET")
        clock.value = datetime.combine(day, clock_time(15, 29, 59), tzinfo=pb.NY)
        bridge.decisions = lambda: [decision_row("last", "LAST", strategy="ORB_V2", con_id=4950,
                                                 created=clock.value - timedelta(seconds=2))]
        bridge.tick()
        assert bridge.brackets["last"].status == "sent"
        clock.value = datetime.combine(day, clock_time(15, 30, 0), tzinfo=pb.NY)
        bridge.decisions = lambda: [decision_row("late", "LATE", strategy="ORB_V2", con_id=4951,
                                                 created=clock.value - timedelta(seconds=2))]
        bridge.tick()
        assert v7c_skip(bridge, "late") == "after 15:30 — no new entries"
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


# ---- state ----------------------------------------------------------------------------------------


def test_t1_the_new_bracket_fields_are_optional_and_older_readers_still_parse(tmp_path: Path) -> None:
    from routers.live_routes import parse_bridge_state
    from trading.operations import session_check as sc
    from trading.research.live_ledger import read_submitted

    new = {"broker_rejection", "stop_repair_detected", "stop_repair_from", "stop_repair_outcome", "market_close_id"}
    assert new <= pb.BRACKET_FIELDS
    assert all(field.default is not MISSING for field in dataclass_fields(pb.Bracket)
               if field.name in new)  # each has a default: a v7c state file loads
    store = pb.StateStore(state_dir=tmp_path / "bridge", legacy_path=None, session_date=SESSION)
    old = {key: value for key, value in pb.asdict(bracket("old", "OLD", status="open", parent=30, filled=100)).items()
           if key not in new}
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps({"schema": pb.STATE_SCHEMA, "session_date": "2026-10-13",
                                      "submitted": {"old": old}}), encoding="utf-8")
    loaded = store.load()["old"]
    assert (loaded.stop_repair_from, loaded.market_close_id, loaded.broker_rejection) == (None, None, "")

    repaired = replace(bracket("rep", "REP", status="open", parent=40, filled=100), stop_id=100, stop_repair_from=42,
                       stop_repair_outcome="repaired", stop_repair_detected=utc(DECIDED), market_close_id=None)
    store.save({"rep": repaired})
    raw = store.path.read_text(encoding="utf-8")
    document = json.loads(raw)
    assert document["schema"] == "paper-bridge-state.v5"
    v7c_fields = pb.BRACKET_FIELDS - new  # the v7c bridge's parse_brackets keeps only its own fields
    assert pb.Bracket(**{key: value for key, value in document["submitted"]["rep"].items() if key in v7c_fields})
    [entry] = parse_bridge_state(raw, session_day=SESSION)  # the dashboard
    assert entry.symbol == "REP"
    assert set(sc.read_state(tmp_path, SESSION)) == {"rep"}  # the session checker
    assert set(read_submitted(store.path)[1]) == {"rep"}  # the live ledger and the v7c report
