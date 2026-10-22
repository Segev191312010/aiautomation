"""The v7c report (plan P3-B3): refused orders and what they would have done, stop slippage, P&L by source.

One hermetic week: a database migrated by the registered migrations with two runs' decision and setup
rows and the signal tracker's outcomes for two of them; bridge state files written by the pinned
bridge's own StateStore and Bracket (tests.test_live_ledger); owner orders queued by the deterministic
parser and validator, claimed and refused through ``trading.execution.manual_orders`` exactly as the
bridge's manual desk does. Every refusal text is the bridge's own (tests.test_paper_bridge). Nothing
reads the real home directory.
"""
from __future__ import annotations

import ast
import asyncio
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from tests.test_live_ledger import _bracket, _save
from tests.test_paper_bridge import manual_ledger, pb, queue_manual
from tests.test_signal_outcomes import TS, _run, _setup, _strategy
from trading.orchestration.session_runner import orb_strategy_spec
from trading.research import v7c_report as v7c
from trading.research.live_ledger import read_live_ledger
from trading.research.replay import FILL_MODEL_ID
from trading.research.v7c_report import (
    BlockedOrder,
    StopExit,
    V7CReport,
    blocked_text,
    collect_v7c_report,
    order_source,
    slippage_by_identity,
    tally,
)
from trading.rules.all_session import vwap_pb_v2_strategy_spec

MONDAY = date(2026, 10, 19)  # the week's first session
WEDNESDAY = date(2026, 10, 21)  # tonight
FILLED_SETUP, UNFILLED_SETUP, PENDING_SETUP = "a" * 64, "b" * 64, "c" * 64
D_FILLED, D_UNFILLED, D_PENDING, D_MONDAY = "decision-filled", "decision-unfilled", "decision-pending", "decision-mon"

# The bridge's refusal texts (process_decision's skip_reason; test_paper_bridge pins them).
WOULD_HAVE = " — would have sent BUY 100 @ 50.00 | stop 49.50 | target 51.00"
OVER_BUDGET = ("v7c_day_risk_budget: realized 0.00 + open 19930.00 + new 50.00 = 19980.00 (3.00%) > 3% of 665000.00"
               f" (source=bot){WOULD_HAVE}")
UNPROTECTED = ("v7c_unprotected_position: AAPL 100 sh held, working stops cover 0 — every risk-increasing order is"
               " refused; close it with /close AAPL or in TWS, or protect it with a stop that stays working (GTC)"
               f" (source=bot){WOULD_HAVE}")
DAILY_LOSS = ("daily_loss_limit: daily loss limit reached (account realized loss 13300.00 >= 2% of net liquidation"
              " 665000.00)")
WRONG_THREAD = f"v7c_wrong_thread: checked off the bridge loop — refused (source=bot){WOULD_HAVE}"
PRE_V7C_LOSS = "daily loss limit reached (realized -13300.00 <= -2% of net liquidation 665000.00)"


def _decision(db: sqlite3.Connection, decision_id: str, *, run_id: str, strategy_id: str, con_id: int,
              fingerprint: str) -> None:
    db.execute(
        """INSERT INTO autotrader_candidate_decisions
           (id, run_id, candidate_id, strategy_id, strategy_version, decision_key, outcome,
            setup_fingerprint, payload_json, created_at)
           VALUES (?, ?, ?, ?, 1, ?, 'shadow_accepted', ?, '{}', ?)""",
        (decision_id, run_id, f"{run_id}:{con_id}", strategy_id, sha256(decision_id.encode()).hexdigest(),
         fingerprint, TS),
    )


def _outcome(db: sqlite3.Connection, *, run_id: str, fingerprint: str, r_multiple: str | None) -> None:
    filled = r_multiple is not None
    db.execute(
        """INSERT INTO autotrader_manager_signal_outcomes
           (run_id, setup_fingerprint, fill_model_id, simulated_at, filled, exit_reason, r_multiple, cost_r)
           VALUES (?, ?, ?, '2026-10-21T22:00:00+00:00', ?, ?, ?, '0.0400')""",
        (run_id, fingerprint, FILL_MODEL_ID, int(filled), "target" if filled else "not_filled", r_multiple),
    )


def _ledger(database: Path) -> None:
    """Three tonight's decisions with setups: one the tracker filled (+1.40R), one it did not, one pending."""
    orb, vwap2 = orb_strategy_spec(), vwap_pb_v2_strategy_spec()
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")  # every row below is the schema's own, references included
        _strategy(db, "strategy-orb", orb)
        _strategy(db, "strategy-vwap2", vwap2)
        _run(db, "run-orb", "strategy-orb", WEDNESDAY)
        _run(db, "run-vwap2", "strategy-vwap2", WEDNESDAY)
        for run_id, strategy_id, spec, kind, fingerprint, con_id, symbol in (
            ("run-orb", "strategy-orb", orb, "orb", FILLED_SETUP, 4931, "BF B"),
            ("run-orb", "strategy-orb", orb, "orb", UNFILLED_SETUP, 493581450, "FIGS"),
            ("run-vwap2", "strategy-vwap2", vwap2, "vwap_pb", PENDING_SETUP, 4931, "BF B"),
        ):
            _setup(db, run_id=run_id, strategy_id=strategy_id, spec=spec, kind=kind, fingerprint=fingerprint,
                   con_id=con_id, symbol=symbol, session_date=WEDNESDAY, signal_end="14:00", entry="50.00",
                   stop="49.50", target="51.00")
        _decision(db, D_FILLED, run_id="run-orb", strategy_id="strategy-orb", con_id=4931, fingerprint=FILLED_SETUP)
        _decision(db, D_UNFILLED, run_id="run-orb", strategy_id="strategy-orb", con_id=493581450,
                  fingerprint=UNFILLED_SETUP)
        _decision(db, D_PENDING, run_id="run-vwap2", strategy_id="strategy-vwap2", con_id=4931,
                  fingerprint=PENDING_SETUP)
        _outcome(db, run_id="run-orb", fingerprint=FILLED_SETUP, r_multiple="1.4000")
        _outcome(db, run_id="run-orb", fingerprint=UNFILLED_SETUP, r_multiple=None)


def _owner_refusals() -> list[str]:
    """Two owner buys the bridge refused with a v7c token, one refused for another reason."""
    from trading.execution import manual_orders as mo

    now = datetime(2026, 10, 21, 10, 0, tzinfo=pb.NY)
    refusals = (
        ("/buy 10 TSLA stop 185", "v7c_day_risk_budget", "v7c_day_risk_budget: realized 0.00 + open 19900.00 + new"
         " 50.10 = 19950.10 (3.00%) > 3% of 665000.00 (source=manual). Nothing sent."),
        ("/buy 10 NVDA stop 185", "daily_loss_limit", f"{DAILY_LOSS} (source=manual). Nothing sent."),
        ("/buy 10 AAPL stop 185", "bot_holds_symbol", "The bot still has orders working on AAPL at IBKR."),
    )
    ids = []
    for number, (text, code, message) in enumerate(refusals, start=1):
        row_id = queue_manual(text, message_id=number, now=now)
        claimed = asyncio.run(mo.claim_next_order(now + timedelta(seconds=2)))
        assert claimed is not None and claimed.id == row_id
        assert asyncio.run(mo.update_order_status(row_id, "rejected", now=now + timedelta(seconds=2),
                                                  reject_code=code, error=message))
        ids.append(row_id)
    return ids


def _stop(decision_id: str, identity: str, avg_entry: str, avg_exit: str, *, stop: str = "49.50") -> Any:
    return _bracket(decision_id, symbol="STP", qty=100, entry="50.00", stop=stop, target="51.00",
                    strategy_name=identity, status="closed", filled=100, avg_entry=avg_entry, exit_filled=100,
                    avg_exit=avg_exit, exit_reason="stop")


def _skipped(decision_id: str, identity: str, reason: str, *, symbol: str = "BF B") -> Any:
    return _bracket(decision_id, symbol=symbol, strategy_name=identity, parent=None, target_id=None, stop_id=None,
                    status="skipped", skipped=True, skip_reason=reason)


@pytest.fixture
def week(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    database = manual_ledger(tmp_path, monkeypatch)  # init_db: every registered migration
    _ledger(database)
    manual = _owner_refusals()
    bridge = tmp_path / "bridge"
    _save(bridge, MONDAY, [
        _skipped(D_MONDAY, "MOMO_HOD_V2", WRONG_THREAD),  # no decision row: no outcome
        _skipped("decision-old", "MOMO_HOD_V2", PRE_V7C_LOSS),  # a pre-v7c refusal: not a v7c block
        _stop("stop-m1", "MOMO_HOD_V2", "50.0000", "49.5000"),  # exactly at its stop: 1.00x
    ])
    _save(bridge, WEDNESDAY, [
        _skipped(D_FILLED, "ORB_V2", OVER_BUDGET),
        _skipped(D_UNFILLED, "ORB_V2", UNPROTECTED, symbol="FIGS"),
        _skipped(D_PENDING, "VWAP_PB_V2", DAILY_LOSS),
        _skipped("decision-shadow", "ORB_V1", pb.SHADOW_ONLY),
        # ORB_V2's stop exits at 0.90x, 0.95x, 1.00x, 1.00x and 1.30x of their planned risk (0.50 a share).
        _stop("stop-o1", "ORB_V2", "50.0000", "49.5500"),
        _stop("stop-o2", "ORB_V2", "50.0000", "49.5250"),
        _stop("stop-o3", "ORB_V2", "50.0000", "49.5000"),
        _stop("stop-o4", "ORB_V2", "50.0000", "49.5000"),
        _stop("stop-o5", "ORB_V2", "50.0000", "49.3500"),
        # Zero or unknown planned risk: filled at the stop itself, and an entry that gapped below it.
        _stop("stop-zero", "ORB_V2", "49.5000", "49.4000"),
        _stop("stop-below", "ORB_V2", "49.4000", "49.3000"),
        _bracket("target-1", symbol="WIN", qty=200, entry="50.00", stop="49.50", target="51.00",
                 strategy_name="VWAP_PB_V2", status="closed", filled=200, avg_entry="50.0000", exit_filled=200,
                 avg_exit="51.0000", exit_reason="target"),
    ])
    return {"database": database, "bridge": bridge, "manual": manual}


def _report(week: dict[str, Any]) -> V7CReport:
    return collect_v7c_report(database=week["database"], bridge_directory=week["bridge"],
                              ledger=read_live_ledger(week["bridge"]), as_of=WEDNESDAY, week_start=MONDAY)


def test_v7c_report_blocked_and_slippage(week: dict[str, Any]) -> None:
    report = _report(week)
    tonight = report.window("tonight")

    assert [(row.source, row.token, row.identity, row.outcome, row.would_have_r) for row in tonight[:3]] == [
        ("bot", "v7c_day_risk_budget", "ORB_V2", "filled", Decimal("1.4000")),
        ("bot", "v7c_unprotected_position", "ORB_V2", "not_filled", Decimal("0")),
        ("bot", "daily_loss_limit", "VWAP_PB_V2", "no_outcome", None),
    ]
    assert sorted((row.source, row.token, row.identity, row.outcome, row.would_have_r) for row in tonight[3:]) == [
        ("manual", "daily_loss_limit", "owner", "no_outcome", None),
        ("manual", "v7c_day_risk_budget", "owner", "no_outcome", None),
    ]
    by_ref = {row.order_ref: row for row in report.blocked}
    assert by_ref[f"autotrader:ORB_V2:{D_FILLED[:12]}"].decision_id == D_FILLED
    assert {f"manual:{row_id}" for row_id in week["manual"][:2]} <= set(by_ref)
    assert f"manual:{week['manual'][2]}" not in by_ref  # refused, but not by the day risk budget
    assert tally(tonight, "bot") == v7c.SourceTally("bot", blocked=3, with_outcome=2, not_filled=1, no_outcome=1,
                                                    would_have_r=Decimal("1.4000"))
    assert tally(tonight, "manual") == v7c.SourceTally("manual", blocked=2, no_outcome=2)

    lines = report.lines()
    assert lines[:3] == [
        "V7C day risk budget (refused risk-increasing orders; would-have R from the signal tracker)",
        "  2026-10-21: V7C blocked 3 bot / 2 manual (would-have +1.40R from 2; 3 without an outcome)",
        "  this week: V7C blocked 4 bot / 2 manual (would-have +1.40R from 2; 4 without an outcome)",
    ]
    assert lines[3:6] == [
        "  STOP SLIPPAGE MOMO_HOD_V2 p90 1.00x (n=1) <= 1.0",
        "  STOP SLIPPAGE ORB_V2 p90 1.30x (n=5) > 1.0",
        "  STOP SLIPPAGE excluded 2 stop exits: zero or unknown risk",
    ]
    orb = {item.identity: item for item in report.slippage}["ORB_V2"]
    assert orb.payload() == {"n": 5, "median": "1.0000", "p90": "1.3000", "max": "1.3000", "excluded": 2,
                             "flag": "stop_slippage_above_factor"}
    momo = {item.identity: item for item in report.slippage}["MOMO_HOD_V2"]
    assert (momo.p90, momo.above_factor) == (Decimal("1"), False)  # at the factor is not above it


def test_no_outcome_rows_are_counted_never_as_zero_r(week: dict[str, Any]) -> None:
    report = _report(week)
    payload = report.payload()
    blocked = payload["blocked"]
    assert blocked["tonight"]["bot"] == {"blocked": 3, "with_outcome": 2, "not_filled": 1, "no_outcome": 1,
                                         "would_have_r": "1.4000"}
    assert blocked["tonight"]["manual"] == {"blocked": 2, "with_outcome": 0, "not_filled": 0, "no_outcome": 2,
                                            "would_have_r": None}
    assert blocked["week"]["bot"]["no_outcome"] == 2 and blocked["all"]["bot"]["blocked"] == 4
    pending = next(row for row in payload["blocked_orders"] if row["decision_id"] == D_PENDING)
    assert (pending["outcome"], pending["would_have_r"]) == ("no_outcome", None)

    # Only rows without an outcome: the R is unknown, never +0.00R.
    unknown = [row for row in report.blocked if row.outcome == "no_outcome"]
    assert blocked_text(unknown) == "V7C blocked 2 bot / 2 manual (would-have unknown: 4 without an outcome)"
    assert "+0.00R" not in blocked_text(unknown)
    # Every row with an outcome: the plan's line shape.
    known = [row for row in report.blocked if row.outcome != "no_outcome"]
    assert blocked_text(known) == "V7C blocked 2 bot / 0 manual (would-have +1.40R)"
    assert blocked_text([]) == "V7C no v7c data (no refused order)"


def test_a_filled_outcome_without_its_r_reads_no_outcome(week: dict[str, Any],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """N18g I-1 (mutant Q07): the schema refuses a filled outcome without its R (CHECK (filled = 1) =
    (r_multiple IS NOT NULL)), so only a reader could hand one over; such a row has no outcome, and its
    JSON says so, never "filled"."""
    from trading.research import promotion_proposals

    real = promotion_proposals.read_shadow_outcomes

    def without_r(database: Any, decision_ids: Any, **kwargs: Any) -> dict[str, Any]:
        found = real(database, decision_ids, **kwargs)
        assert found[D_FILLED] == promotion_proposals.ShadowOutcome(filled=True, gross_r=Decimal("1.4000"))
        return {**found, D_FILLED: promotion_proposals.ShadowOutcome(filled=True, gross_r=None)}

    monkeypatch.setattr(promotion_proposals, "read_shadow_outcomes", without_r)
    payload = _report(week).payload()

    [row] = [item for item in payload["blocked_orders"] if item["decision_id"] == D_FILLED]
    assert (row["outcome"], row["would_have_r"], row["token"]) == ("no_outcome", None, "v7c_day_risk_budget")
    assert payload["blocked"]["tonight"]["bot"] == {"blocked": 3, "with_outcome": 1, "not_filled": 1,
                                                    "no_outcome": 2, "would_have_r": "0.0000"}


def test_the_source_tag_is_the_order_ref_prefix() -> None:
    from trading.execution import manual_orders as mo

    row_id = "0123456789abcdef0123456789abcdef"
    assert order_source(v7c.bot_order_ref("ORB_V2", D_FILLED)) == "bot"
    assert order_source("autotrader:MOMO_HOD_V2:abcdefghijkl") == "bot"
    assert order_source(mo.order_ref(row_id)) == "manual" and v7c.manual_order_ref(row_id) == mo.order_ref(row_id)
    for other in ("", "autotrader:", "manual:", "at1:bracket:P", "MANUAL:abc", "Autotrader:X:1", None, 7):
        assert order_source(other) is None, other
    # The bridge's own refs: Bridge.ref for a bracket, parse_ref reading it back.
    bracket = _bracket(D_FILLED, strategy_name="ORB_V2")
    assert v7c.bot_order_ref("ORB_V2", D_FILLED) == pb.Bridge.ref(None, bracket)
    assert pb.parse_ref(v7c.bot_order_ref(" ORB_V2 ", D_FILLED)) == ("ORB_V2", D_FILLED[:12])


def test_the_report_vocabulary_is_the_pinned_bridges() -> None:
    from trading.execution import manual_orders as mo

    assert (v7c.BOT_REF_PREFIX, v7c.MANUAL_REF_PREFIX) == (pb.ORDER_REF_PREFIX, pb.MANUAL_REF_PREFIX)
    assert v7c.MANUAL_REF_PREFIX == mo.ORDER_REF_PREFIX
    assert (v7c.DAY_RISK_BUDGET_PCT, v7c.STOP_SLIPPAGE_FACTOR) == (pb.DAY_RISK_BUDGET_PCT, pb.STOP_SLIPPAGE_FACTOR)
    assert (v7c.V7C_DAY_RISK_BUDGET, v7c.V7C_UNPROTECTED_POSITION, v7c.V7C_WRONG_THREAD, v7c.DAILY_LOSS_LIMIT,
            v7c.V7C_BUDGET_OK, v7c.V7C_BUDGET_OVER, v7c.V7C_STOP_WIDENED, v7c.V7C_BUDGET_INVARIANT_BROKEN) == (
        pb.V7C_DAY_RISK_BUDGET, pb.V7C_UNPROTECTED_POSITION, pb.V7C_WRONG_THREAD, pb.DAILY_LOSS_LIMIT,
        pb.V7C_BUDGET_OK, pb.V7C_BUDGET_OVER, pb.V7C_STOP_WIDENED, pb.V7C_BUDGET_INVARIANT_BROKEN)
    # Every token that can start a refusal's skip_reason or reject_code: budget_check's returns, and
    # its daily loss refusal (``{loss}``, daily_loss_problem's text, which the gate also skips with).
    source = Path(pb.__file__).read_text(encoding="utf-8")
    start = source.index("    def budget_check(")
    body = source[start:source.index("\n    def budget_problem(", start)]
    refusing = set(re.findall(r'return \(?f"\{(\w+)\}', body))
    assert refusing == {"V7C_DAY_RISK_BUDGET", "V7C_UNPROTECTED_POSITION", "V7C_WRONG_THREAD", "loss"}
    assert set(v7c.BLOCK_TOKENS) == {getattr(pb, name) for name in refusing - {"loss"}} | {pb.DAILY_LOSS_LIMIT}
    assert pb.Bridge.daily_loss_problem(Decimal("13300"), Decimal("665000")).startswith(f"{pb.DAILY_LOSS_LIMIT}:")
    assert v7c.block_token(pb.Bridge.daily_loss_problem(Decimal("13300"), Decimal("665000"))) == "daily_loss_limit"


def test_block_tokens_start_the_reason_and_a_pre_v7c_reason_is_none() -> None:
    assert [v7c.block_token(reason) for reason in (OVER_BUDGET, UNPROTECTED, DAILY_LOSS, WRONG_THREAD)] == [
        "v7c_day_risk_budget", "v7c_unprotected_position", "daily_loss_limit", "v7c_wrong_thread"]
    assert v7c.block_token("v7c_day_risk_budget") == "v7c_day_risk_budget"  # a manual reject_code
    for other in (PRE_V7C_LOSS, pb.SHADOW_ONLY, "identity_stopped:ORB_V2", "v7c_budget_ok AAPL (ORB_V2): realized",
                  "v7c_day_risk_budgets: x", "", None):
        assert v7c.block_token(other) is None, other


def test_stop_slippage_statistics_and_exclusions() -> None:
    def exits(*ratios: str | None, identity: str = "ORB_V2") -> list[StopExit]:
        return [StopExit(WEDNESDAY, f"d{index}", identity, "X", None if ratio is None else Decimal(ratio))
                for index, ratio in enumerate(ratios)]

    [item] = slippage_by_identity(exits("1.30", "0.90", "1.00", "0.95", "1.00", None))
    assert (item.n, item.median, item.p90, item.max, item.excluded) == (
        5, Decimal("1.00"), Decimal("1.30"), Decimal("1.30"), 1)
    assert item.above_factor and item.line() == "STOP SLIPPAGE ORB_V2 p90 1.30x (n=5) > 1.0"
    [even] = slippage_by_identity(exits(*(["1.00"] * 9), "1.20"))  # nearest rank: the 9th of 10
    assert (even.median, even.p90, even.above_factor) == (Decimal("1.00"), Decimal("1.00"), False)
    [only_excluded] = slippage_by_identity(exits(None, None))
    assert (only_excluded.n, only_excluded.p90, only_excluded.line(), only_excluded.excluded) == (0, None, None, 2)
    # The measure itself: at the stop 1x, a gap past it more, zero or negative planned risk excluded.
    ratio = v7c.stop_ratio
    assert ratio(filled=100, exit_filled=100, avg_entry=Decimal("50"), avg_exit=Decimal("49.5"),
                 stop=Decimal("49.5")) == Decimal("1")
    assert ratio(filled=100, exit_filled=100, avg_entry=Decimal("50"), avg_exit=Decimal("49.35"),
                 stop=Decimal("49.5")) == Decimal("1.3")
    for avg_entry, filled, exit_filled in (("49.5", 100, 100), ("49.4", 100, 100), ("0", 100, 100),
                                           ("50", 0, 0), ("50", 100, 60)):
        assert ratio(filled=filled, exit_filled=exit_filled, avg_entry=Decimal(avg_entry), avg_exit=Decimal("49"),
                     stop=Decimal("49.5")) is None, (avg_entry, filled, exit_filled)


def test_pnl_by_source_and_the_unknowns_are_named(week: dict[str, Any]) -> None:
    report = _report(week)
    lines = report.lines()
    # Tonight: seven ORB stop exits (-45.00 -47.50 -50.00 -50.00 -65.00 -10.00 -10.00) and a VWAP target
    # +200.00, each bracket two orders at IBKR Pro's $1 minimum; Monday adds a MOMO stop (-50.00).
    assert lines[-2:] == [
        "  P&L by source 2026-10-21: bot -93.50 net (8 closed, gross -77.50) · manual unknown (not recorded)",
        "  P&L by source this week: bot -145.50 net (9 closed, gross -127.50) · manual unknown (not recorded)",
    ]
    payload = report.payload()
    tonight = payload["pnl_by_source"]["tonight"]
    assert tonight["manual"] is None and tonight["bot"]["trades"] == 8
    trades = [trade for trade in read_live_ledger(week["bridge"]).trades if trade.session_date == WEDNESDAY]
    assert Decimal(tonight["bot"]["gross"]) == sum((trade.gross_pnl for trade in trades), Decimal("0"))
    assert payload["pnl_by_source"]["week"]["bot"]["trades"] == 9
    assert payload["unknown"] == list(v7c.GAPS) and len(v7c.GAPS) == 4
    assert payload["manual_ledger"] == "read" and payload["sessions"] == ["2026-10-19", "2026-10-21"]


def test_absent_data_reads_no_v7c_data_never_nothing(tmp_path: Path) -> None:
    empty = collect_v7c_report(database=tmp_path / "none.db", bridge_directory=tmp_path / "none", ledger=
                               read_live_ledger(tmp_path / "none"), as_of=WEDNESDAY, week_start=MONDAY)
    lines = empty.lines()
    assert lines[1:4] == [
        "  2026-10-21: V7C no v7c data (no refused order)",
        "  this week: V7C no v7c data (no refused order)",
        "  STOP SLIPPAGE no v7c data (no stop exit with a known risk)",
    ]
    assert "  manual-order ledger absent: owner refusals unknown" in lines
    assert not (tmp_path / "none.db").exists()  # a read-only open never creates a database
    failed = v7c.failed_report(WEDNESDAY, "failed_runtimeerror")
    assert failed.lines() == [v7c.HEADER, "  V7C no v7c data: report unreadable (failed_runtimeerror)"]
    assert failed.payload()["readable"] is False and failed.payload()["lines"] == failed.lines()
    assert v7c.report_lines(None) == [v7c.HEADER, "  V7C no v7c data: report not_collected"]


def test_a_broken_outcome_table_leaves_the_rows_counted_without_an_outcome(week: dict[str, Any]) -> None:
    with sqlite3.connect(week["database"]) as db:
        db.execute("ALTER TABLE autotrader_manager_signal_outcomes RENAME TO moved_outcomes")
    report = _report(week)
    assert [row.outcome for row in report.window("tonight") if row.source == "bot"] == ["no_outcome"] * 3
    assert "V7C blocked 3 bot / 2 manual (would-have unknown: 5 without an outcome)" in report.lines()[1]


def test_the_report_only_reads() -> None:
    source = Path(v7c.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden = ("trading.authority", "trading.execution", "trading.operations", "trading.storage.repository",
                 "trading.storage.runtime_fence", "db.core", "aiosqlite", "os", "subprocess")
    assert not [name for name in imported if name.startswith(forbidden)], imported
    for statement in ("INSERT", "UPDATE ", "DELETE", "CREATE ", "DROP ", "mode=rw", "write_text", "write_bytes",
                      "open("):
        assert statement not in source, statement
    assert "?mode=ro" in source


def test_a_manual_row_with_an_unreadable_time_counts_in_all_only() -> None:
    row = BlockedOrder(session_date=None, source="manual", token="v7c_day_risk_budget", order_ref="manual:x",
                       identity="owner", symbol="X", decision_id=None, outcome="no_outcome", would_have_r=None)
    report = V7CReport(as_of=WEDNESDAY, week_start=MONDAY, blocked=(row,))
    assert (report.window("tonight"), report.window("week"), report.window("all")) == ((), (), (row,))
    assert replace(row, session_date=WEDNESDAY) in V7CReport(
        as_of=WEDNESDAY, week_start=MONDAY, blocked=(replace(row, session_date=WEDNESDAY),)).window("week")
