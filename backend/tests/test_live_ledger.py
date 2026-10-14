"""Live R ledger: real PAPER brackets in R, read from the bridge's state files.

The fixtures are written by the pinned bridge's own StateStore and Bracket
(tests.test_paper_bridge loads scripts_local/paper_bridge.py), so the ledger
reads exactly the format the bridge writes. Nothing touches the real state
directory under ~/.local/share.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path

import pytest

from tests.test_paper_bridge import pb as bridge
from trading.operations.paths import UserPaths
from trading.research.live_ledger import (
    bridge_state_directory,
    ledger_lines,
    read_live_ledger,
)

MONDAY = date(2026, 10, 12)
TUESDAY = date(2026, 10, 13)


def _bracket(decision_id: str, **values) -> object:
    base = bridge.Bracket(
        decision_id=decision_id,
        symbol="BF B",
        con_id=4931,
        primary_exchange="NYSE",
        strategy="momo",
        qty=100,
        entry="26.77",
        stop="26.51",
        target="27.29",
        parent=101,
        target_id=102,
        stop_id=103,
        status="sent",
        strategy_name="MOMO_HOD_V2",
    )
    return replace(base, **values)


def _save(directory: Path, day: date, brackets: list) -> Path:
    store = bridge.StateStore(state_dir=directory, legacy_path=None, session_date=day)
    store.save({item.decision_id: item for item in brackets})
    return store.path


@pytest.fixture
def state_directory(tmp_path) -> Path:
    directory = tmp_path / "bridge"
    _save(
        directory,
        MONDAY,
        [
            # Stopped: 100 sh bought at 26.77, sold at the 26.51 stop: -1R before costs.
            _bracket("d-stop", status="closed", filled=100, avg_entry="26.7700", exit_filled=100,
                     avg_exit="26.5100", exit_reason="stop", realized_pnl="-26.00"),
            # Target: 200 sh, 0.50 risk, +1.00: +2R.
            _bracket("d-target", symbol="FIGS", con_id=493581450, qty=200, entry="50.00",
                     stop="49.50", target="51.00", status="closed", filled=200,
                     avg_entry="50.0000", exit_filled=200, avg_exit="51.0000", exit_reason="target",
                     realized_pnl="200.00"),
            # Never filled, cancelled at its TTL.
            _bracket("d-unfilled", symbol="AAPL", status="cancelled"),
            # A v1 entry (no qty): never traded, never managed.
            _bracket("d-skipped", skipped=True, status="skipped", skip_reason="shadow-only strategy"),
        ],
    )
    _save(
        directory,
        TUESDAY,
        [
            # Partial fill flattened at 15:45: 60 sh, +0.30 on 0.50 risk: +0.6R.
            _bracket("d-flatten", symbol="KO", qty=100, entry="50.00", stop="49.50", target="51.00",
                     status="closed", filled=60, avg_entry="50.0000", exit_filled=60,
                     avg_exit="50.3000", exit_reason="flatten", realized_pnl="18.00"),
            _bracket("d-open", symbol="NVDA", status="open", filled=100, avg_entry="26.7700"),
            _bracket("d-attention", symbol="AMD", status="needs_attention", filled=50,
                     exit_filled=60, attention_reason="exit fills 60 exceed entry fills 50"),
        ],
    )
    return directory


def test_closed_brackets_become_r_after_commissions(state_directory):
    ledger = read_live_ledger(state_directory)

    assert ledger.sessions == (MONDAY, TUESDAY)
    trades = {trade.decision_id: trade for trade in ledger.trades}
    assert set(trades) == {"d-stop", "d-target", "d-flatten"}
    stop = trades["d-stop"]
    # $1 minimum on each 100-share order: $2.00 over $26.00 of risk.
    assert (stop.gross_pnl, stop.gross_r, stop.commissions, stop.cost_r) == (
        Decimal("-26.0000"), Decimal("-1.0000"), Decimal("2.00"), Decimal("0.0770"),
    )
    assert stop.net_r == Decimal("-1.0770")
    target = trades["d-target"]
    assert (target.gross_r, target.commissions, target.cost_r, target.net_r) == (
        Decimal("2.0000"), Decimal("2.00"), Decimal("0.0200"), Decimal("1.9800"),
    )
    flatten = trades["d-flatten"]
    # Risk is the 60 shares actually bought, at the bracket's own 0.50 a share.
    assert (flatten.shares, flatten.gross_r, flatten.cost_r, flatten.exit_reason) == (
        60, Decimal("0.6000"), Decimal("0.0667"), "flatten",
    )
    assert dict(ledger.open) == {"MOMO_HOD_V2": 1}
    assert dict(ledger.attention) == {"MOMO_HOD_V2": 1}
    assert dict(ledger.unfilled) == {"MOMO_HOD_V2": 1}
    assert ledger.skipped == 1 and ledger.unreadable == ()
    (row,) = ledger.by_strategy()
    assert (row.strategy, row.trades, row.wins) == ("MOMO_HOD_V2", 3, 2)
    assert row.net_r == Decimal("1.4363")


def test_the_weekly_lines_show_each_strategy_and_the_week(state_directory):
    lines = ledger_lines(read_live_ledger(state_directory), week_start=TUESDAY)

    assert lines == [
        "2 sessions 2026-10-12..2026-10-13",
        "MOMO_HOD_V2: 3 trades, net +1.44R (gross +1.60R, costs 0.16R), +0.48R/trade, 2/3 won;"
        " 1 open, 1 need attention, 1 unfilled",
        "this week: 1 trades, net +0.53R",
    ]
    assert ledger_lines(read_live_ledger(state_directory / "missing")) == ["no bridge sessions recorded"]


def test_untrusted_files_and_brackets_are_named_never_guessed(state_directory, tmp_path):
    # Another session's document under this date's name.
    misfiled = json.loads((state_directory / "2026-10-12.json").read_text())
    misfiled["session_date"] = "2026-10-09"
    (state_directory / "2026-10-14.json").write_text(json.dumps(misfiled))
    # A bracket that closed selling more than it bought.
    broken = json.loads((state_directory / "2026-10-13.json").read_text())
    broken["submitted"]["d-flatten"]["exit_filled"] = 70
    (state_directory / "2026-10-13.json").write_text(json.dumps(broken))
    # A link to a file elsewhere is never followed; stray names are ignored.
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"schema": "paper-bridge-state.v5", "submitted": {}}))
    os.symlink(outside, state_directory / "2026-10-15.json")
    (state_directory / "paper_bridge_state.json").write_text("{}")
    (state_directory / "2026-10-16.json").write_text("not json")

    ledger = read_live_ledger(state_directory)

    assert ledger.unreadable == (
        "2026-10-13.json:d-flatten",
        "2026-10-14.json",
        "2026-10-15.json",
        "2026-10-16.json",
    )
    assert ledger.sessions == (MONDAY, TUESDAY)
    assert {trade.decision_id for trade in ledger.trades} == {"d-stop", "d-target"}
    assert "not read (see the research log): 2026-10-13.json:d-flatten" in ledger_lines(ledger)[-1]


def test_the_ledger_reads_the_bridges_own_directory_by_default(tmp_path):
    paths = UserPaths(home=tmp_path)
    assert bridge_state_directory(paths) == tmp_path / ".local" / "share" / "autotrader-v1" / "bridge"
    assert bridge.STATE_DIR.relative_to(bridge.DATA_DIR) == Path("bridge")
    assert bridge.STATE_SCHEMA.startswith("paper-bridge-state.")
    assert read_live_ledger(bridge_state_directory(paths)).trades == ()


def test_the_ledger_never_writes_the_state_files(state_directory):
    before = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in state_directory.iterdir()}
    for path in state_directory.iterdir():
        path.chmod(0o400)
    state_directory.chmod(0o500)
    try:
        read_live_ledger(state_directory)
    finally:
        state_directory.chmod(0o700)
        for path in state_directory.iterdir():
            path.chmod(0o600)
    after = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in state_directory.iterdir()}
    assert after == before
