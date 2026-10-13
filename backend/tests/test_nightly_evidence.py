"""The nightly evidence run after a full replay, and the weekly summary it feeds.

One hermetic night: the real 2026-10-12 captures in the research store, a
phase-1 ledger (migrations plus the migration-15 draft) with ORB_V1's two real
setups and a VWAP_PB_V2 setup, and bridge state files written by the pinned
bridge's own StateStore. Nothing reads the real home directory.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.test_live_ledger import MONDAY, TUESDAY, _bracket, _save
from tests.test_research import SESSION, _capture_store
from tests.test_signal_outcomes import LATER_SESSION, _phase1_database, _run, _setup, _strategy
from trading.contracts import RiskPolicy, ShadowRuntimeConfig
from trading.operations import shadow_service
from trading.operations.notifier import utf16_length
from trading.orchestration.session_runner import orb_strategy_spec
from trading.research import bars_store, report_notify
from trading.research.nightly import (
    REPLAY_WINDOW,
    SWEEP_LATEST_START,
    NightlyEnvironment,
    sweep_allowed,
    week_start,
)
from trading.research.replay import run_replay_cli
from trading.rules.all_session import vwap_pb_v2_strategy_spec

NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc


def _et(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NEW_YORK).astimezone(UTC)


def _config() -> ShadowRuntimeConfig:
    return ShadowRuntimeConfig(
        paper_account_id="DU1234567",
        data_client_id=17,
        order_client_id=18,
        model_id="claude-opus-5-5",
        prompt_id="orb-review-v1",
        calendar_id="XNYS",
        calendar_version="2026.1",
        commission_policy_id="ibkr-fixed",
        commission_policy_version=1,
    )


def _prints(key: str) -> dict[str, str]:
    prompt = shadow_service.STRATEGIES[key][2]
    return shadow_service.policy_fingerprints(_config(), RiskPolicy(), prompt_id=prompt, strategy_name=key)


@pytest.fixture
def night(tmp_path, monkeypatch) -> dict[str, Any]:
    root = tmp_path / "research"
    monkeypatch.setattr(bars_store.ResearchPaths, "default", classmethod(lambda cls: cls(root=root)))
    _capture_store(tmp_path)
    database = _phase1_database(tmp_path / "autotrader.db")
    orb, vwap2 = orb_strategy_spec(), vwap_pb_v2_strategy_spec()
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _strategy(connection, "strategy-orb", orb)
        _strategy(connection, "strategy-vwap2", vwap2)
        _run(connection, "run-orb", "strategy-orb", SESSION, prints=_prints("orb"))
        _run(connection, "run-vwap2", "strategy-vwap2", SESSION, prints=_prints("vwap-v2"))
        _run(connection, "run-orb-later", "strategy-orb", LATER_SESSION, prints=_prints("orb"))
        _setup(connection, run_id="run-orb", strategy_id="strategy-orb", spec=orb, kind="orb",
               fingerprint="a" * 64, con_id=4931, symbol="BF B", session_date=SESSION,
               signal_end="14:00", entry="26.6", stop="26.51", target="26.78")
        _setup(connection, run_id="run-orb", strategy_id="strategy-orb", spec=orb, kind="orb",
               fingerprint="b" * 64, con_id=493581450, symbol="FIGS", session_date=SESSION,
               signal_end="14:15", entry="16.93", stop="16.77", target="17.25")
        _setup(connection, run_id="run-vwap2", strategy_id="strategy-vwap2", spec=vwap2,
               kind="vwap_pb", fingerprint="c" * 64, con_id=493581450, symbol="FIGS",
               session_date=SESSION, signal_end="17:25", entry="15.00", stop="14.80",
               target="15.40")
        _setup(connection, run_id="run-orb-later", strategy_id="strategy-orb", spec=orb,
               kind="orb", fingerprint="d" * 64, con_id=4931, symbol="BF B",
               session_date=LATER_SESSION, signal_end="14:00", entry="26.6", stop="26.51",
               target="26.78")
    bridge = tmp_path / "bridge"
    _save(bridge, MONDAY, [
        _bracket("d-stop", status="closed", filled=100, avg_entry="26.7700", exit_filled=100,
                 avg_exit="26.5100", exit_reason="stop"),
        _bracket("d-target", symbol="FIGS", qty=200, entry="50.00", stop="49.50", target="51.00",
                 status="closed", filled=200, avg_entry="50.0000", exit_filled=200,
                 avg_exit="51.0000", exit_reason="target"),
    ])
    _save(bridge, TUESDAY, [
        _bracket("d-flatten", symbol="KO", entry="50.00", stop="49.50", target="51.00",
                 status="closed", filled=60, avg_entry="50.0000", exit_filled=60,
                 avg_exit="50.3000", exit_reason="flatten"),
    ])
    sent: list[str] = []

    def fake_notify(summary: str, **_kwargs: Any) -> str:
        sent.append(summary)
        return report_notify.SENT

    monkeypatch.setattr(report_notify, "notify_summary", fake_notify)
    return {"root": root, "database": database, "bridge": bridge, "sent": sent}


def _environment(night: dict[str, Any], moment: datetime) -> NightlyEnvironment:
    return NightlyEnvironment(database=night["database"], bridge_directory=night["bridge"], clock=lambda: moment)


def test_a_full_replay_runs_the_evidence_and_leads_the_weekly_summary_with_it(night, capsys):
    assert run_replay_cli([], notify="weekly", environment=_environment(night, _et(TUESDAY, 17, 40))) == 0

    (summary,) = night["sent"]
    sections = [block.splitlines() for block in summary.split("\n\n")]
    evidence, windows, live_r = sections[0], sections[1], sections[2]
    assert evidence[0] == "Paper promotion (paper-promotion-v1; reports only, nothing is granted)"
    assert [line.split(":")[0] for line in evidence[1:8]] == [
        "ORB_V1", "MOMO_HOD_V2", "VWAP_PB_V1", "GAP_GO_V1", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2",
    ]
    # Two days of trades are needed for a bound: until then only the counts show.
    assert evidence[1] == (
        "ORB_V1: not eligible: live sessions 2/20, replay sessions 1/20, replay trades 2/100,"
        " live setups 2/30; AI: too little data"
    )
    assert evidence[7].startswith("GAP_GO_V2: not eligible: live sessions 0/20, no replay (needs extended bars)")
    assert evidence[8] == (
        "signal outcomes (replay-bracket-v1): 3 new (2 filled) of 4 untracked setups;"
        " 1 wait for recorded bars (oldest 2026-10-13)"
    )
    assert windows[0] == "Window research (replay, rules-only, net R after costs; * = outside the V1 window)"
    assert "ORB (1 sessions, 2 symbol-days)" in windows
    assert "GAP_GO: not swept (needs extended bars)" in windows
    assert windows[-2:] == ["Live tracked setups by signal hour (net R)", "  ORB_V1: 09h 1 -1.22R · 10h 1 +1.94R"]
    assert live_r == [
        "Live R (PAPER bridge, after commissions)",
        "  2 sessions 2026-10-12..2026-10-13",
        "  MOMO_HOD_V2: 3 trades, net +1.44R (gross +1.60R, costs 0.16R), +0.48R/trade, 2/3 won",
        "  this week: 3 trades, net +1.44R",
    ]
    assert sections[3][0] == "Research replay 2026-10-12..2026-10-12: 1 sessions, 2 symbols"
    assert summary in capsys.readouterr().out

    directory = night["root"] / "reports" / "2026-10-12"
    evidence_report = json.loads((directory / "evidence.json").read_text())
    assert [item["strategy"] for item in evidence_report["promotion"]] == [
        "ORB_V1", "MOMO_HOD_V2", "VWAP_PB_V1", "GAP_GO_V1", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2",
    ]
    assert {item["status"] for item in evidence_report["promotion"]} == {"NOT_ELIGIBLE"}
    assert {item["grants"] for item in evidence_report["promotion"]} == {
        "nothing: eligibility is evidence for the owner's review only"
    }
    assert evidence_report["tracker"]["written"] == 3
    assert evidence_report["live_hours"] == {
        "ORB_V1": [{"hour": 9, "trades": 1, "total_r": "-1.2223"}, {"hour": 10, "trades": 1, "total_r": "1.9375"}]
    }
    windows_report = json.loads((directory / "windows.json").read_text())
    assert [item["family"] for item in windows_report["families"]] == ["orb", "vwap_pb", "momo_hod", "gap_go"]
    assert json.loads((directory / "live-r.json").read_text())["sessions"] == ["2026-10-12", "2026-10-13"]
    assert (directory / "evidence.txt").read_text().startswith("Paper promotion (paper-promotion-v1")
    for name in ("evidence.json", "windows.json", "live-r.json", "evidence.txt"):
        assert (directory / name).stat().st_mode & 0o777 == 0o600


def test_the_night_is_idempotent_and_the_sweep_yields_to_the_window_end(night):
    run_replay_cli([], environment=_environment(night, _et(TUESDAY, 17, 40)))
    with sqlite3.connect(night["database"]) as connection:
        assert connection.execute("SELECT count(*) FROM autotrader_manager_signal_outcomes").fetchone() == (3,)

    # A restart at 18:10 inside the replay window: no new outcomes, no sweep.
    assert run_replay_cli([], environment=_environment(night, _et(TUESDAY, 18, 10))) == 0

    with sqlite3.connect(night["database"]) as connection:
        assert connection.execute("SELECT count(*) FROM autotrader_manager_signal_outcomes").fetchone() == (3,)
    directory = night["root"] / "reports" / "2026-10-12"
    windows_report = json.loads((directory / "windows.json").read_text())
    assert windows_report["families"] == []
    assert windows_report["note"] == "not swept tonight: started after 18:00 ET (the replay window ends 18:30)"
    evidence_report = json.loads((directory / "evidence.json").read_text())
    assert (evidence_report["tracker"]["considered"], evidence_report["tracker"]["written"]) == (1, 0)


def test_an_explicit_strategy_list_replays_only(night):
    assert run_replay_cli(["orb"], notify="weekly", environment=_environment(night, _et(TUESDAY, 17, 40))) == 0
    (summary,) = night["sent"]
    assert summary.startswith("Research replay")
    with sqlite3.connect(night["database"]) as connection:
        assert connection.execute("SELECT count(*) FROM autotrader_manager_signal_outcomes").fetchone() == (0,)


def test_the_weekly_message_is_one_telegram_message_led_by_the_evidence(night):
    run_replay_cli([], notify="weekly", environment=_environment(night, _et(TUESDAY, 17, 40)))
    (summary,) = night["sent"]

    message = report_notify.summary_message(
        summary, now=_et(TUESDAY, 17, 45), newest_session=SESSION, weekly=True
    )

    assert utf16_length(message) <= 4096
    lines = message.splitlines()
    assert lines[0].startswith("📊 Weekly research summary")
    assert "Paper promotion (paper-promotion-v1; reports only, nothing is granted)" in lines[:5]


def test_the_sweep_guard_applies_only_inside_the_supervised_replay_window():
    assert REPLAY_WINDOW == (time(17, 35), time(18, 30)) and SWEEP_LATEST_START == time(18, 0)
    assert sweep_allowed(_et(TUESDAY, 17, 40)) == (True, "")
    assert sweep_allowed(_et(TUESDAY, 18, 0))[0] is True
    assert sweep_allowed(_et(TUESDAY, 18, 1))[0] is False
    assert sweep_allowed(_et(TUESDAY, 20, 0))[0] is True  # a manual run after the window
    assert week_start(date(2026, 10, 16)) == date(2026, 10, 12)
    assert week_start(date(2026, 11, 27)) == date(2026, 11, 23)
