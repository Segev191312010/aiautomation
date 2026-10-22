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
    # Section 4 is the phase-3 readiness block (tests/test_overlay.py); the replay follows it.
    assert sections[4][0] == "Research replay 2026-10-12..2026-10-12: 1 sessions, 2 symbols"
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


def test_a_failing_evidence_step_is_named_and_the_rest_still_counts(night, monkeypatch):
    from trading.research import nightly

    async def broken(**_kwargs: Any) -> None:
        raise RuntimeError("database is locked")

    def broken_sweep(_store: Any) -> None:
        raise ValueError("bad bar")

    monkeypatch.setattr(nightly, "track_outcomes", broken)
    monkeypatch.setattr(nightly, "sweep_all", broken_sweep)

    assert run_replay_cli([], notify="weekly", environment=_environment(night, _et(TUESDAY, 17, 40))) == 0

    (summary,) = night["sent"]
    assert "signal outcomes: not tracked (failed_runtimeerror)" in summary
    assert "not swept tonight: failed_valueerror" in summary
    assert "MOMO_HOD_V2: 3 trades, net +1.44R" in summary  # live R still read
    directory = night["root"] / "reports" / "2026-10-12"
    evidence_report = json.loads((directory / "evidence.json").read_text())
    assert evidence_report["tracker"]["reason"] == "failed_runtimeerror"
    assert len(evidence_report["promotion"]) == 7


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


# --------------------------------------------------------------------------
# Live/shadow decision parity (T-9): the replay raises the mismatch fence
# --------------------------------------------------------------------------


def _equality(*categories: str):
    from trading.replay import ReplayEqualityReport, ReplayMismatch

    return ReplayEqualityReport(
        recorded_fingerprint="a" * 64,
        replayed_fingerprint="b" * 64,
        mismatches=tuple(
            ReplayMismatch(category=category, identity=f"candidate-{index}", reason="fingerprint_mismatch")
            for index, category in enumerate(categories)
        ),
    )


@pytest.fixture
def fence_db(tmp_path, monkeypatch) -> Path:
    import asyncio

    from config import cfg
    from db.core import init_db

    path = tmp_path / "fences.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    asyncio.run(init_db())
    return path


def _fence_rows(database: Path) -> dict[str, tuple[int, str]]:
    with sqlite3.connect(database) as connection:
        return {
            name: (stopped, reason)
            for name, stopped, reason in connection.execute(
                "SELECT name, stopped, reason FROM autotrader_execution_fences"
            )
        }


@pytest.fixture
def fences_live(monkeypatch) -> None:
    """The live auto-fence (P1-B.4's switch, shipped True since P1-B.4), pinned on explicitly."""
    from trading.operations import sentinel

    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", True)


def test_without_a_comparer_the_parity_step_says_not_compared_and_fences_nothing(night, fence_db):
    from trading.research.nightly import PARITY_NOT_COMPARED_NOTE

    assert run_replay_cli([], environment=_environment(night, _et(TUESDAY, 17, 40))) == 0

    evidence = json.loads((night["root"] / "reports" / "2026-10-12" / "evidence.json").read_text())
    assert evidence["decision_parity"] == {
        "status": "not_compared", "mismatches": {}, "fenced": [], "already_fenced": [], "failed": {},
        "note": PARITY_NOT_COMPARED_NOTE, "not_compared": {}, "would_fence": [],
    }
    assert _fence_rows(fence_db) == {}


def test_in_log_only_a_replay_mismatch_writes_no_fence_and_the_report_says_would_fence(
    night, fence_db, caplog, monkeypatch
):
    """The switch off (log-only): the nightly hook judges as when live, through
    the same ``fence_identity``, writes no row, and records WOULD_FENCE."""
    import logging

    from trading.operations import sentinel
    from trading.research.nightly import fence_replay_mismatches
    from trading.storage.runtime_fence import SQLitePaperKillStore

    # P1-B.4 (owner): the Sentinel ships live, so log-only is set explicitly here.
    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", False)
    caplog.set_level(logging.INFO)

    def compare(_results):
        return {
            "ORB_V2": _equality("decisions", "decisions"),
            "ORB_V1": _equality("decisions"),  # shadow-only: reported, never even a would-fence
        }

    environment = NightlyEnvironment(
        database=night["database"], bridge_directory=night["bridge"], clock=lambda: _et(TUESDAY, 17, 40),
        compare_decisions=compare, fence_store=SQLitePaperKillStore(),
    )
    assert run_replay_cli([], environment=environment) == 0

    assert _fence_rows(fence_db) == {}
    report = night["root"] / "reports" / "2026-10-12" / "evidence.json"
    assert json.loads(report.read_text())["decision_parity"] == {
        "status": "compared", "mismatches": {"ORB_V1": 1, "ORB_V2": 2}, "fenced": [],
        "already_fenced": [], "failed": {}, "note": "", "not_compared": {}, "would_fence": ["ORB_V2"],
    }
    messages = [record.getMessage() for record in caplog.records]
    assert "sentinel would fence ORB_V2: replay mismatch 2 decisions" in messages
    assert not any(message.startswith("fenced ORB_V2") for message in messages)

    # An identity the owner already stopped is still "would fence": log-only never reads the store.
    class NoStore:
        async def stop_identity(self, **_kwargs):
            raise AssertionError("log-only reached the fence store")

    import asyncio

    parity = asyncio.run(fence_replay_mismatches({"GAP_GO_V2": 1, "ORB_V1": 3}, store=NoStore()))
    assert (parity.would_fence, parity.fenced, parity.already_fenced, parity.failed) == (("GAP_GO_V2",), (), (), {})


@pytest.mark.usefixtures("fences_live")
def test_a_replay_mismatch_fences_the_sendable_identity_once_and_the_report_records_it(night, fence_db):
    from trading.storage.runtime_fence import SQLitePaperKillStore

    compared: list[tuple[str, ...]] = []

    def compare(results):
        compared.append(tuple(result.strategy for result in results))
        return {
            # Two decisions differ (a setup difference is not a decision).
            "ORB_V2": _equality("decisions", "setups", "decisions"),
            # A shadow-only identity sends nothing: reported, never fenced.
            "ORB_V1": _equality("decisions"),
            "VWAP_PB_V2": _equality(),
            "GAP_GO_V2": _equality("fills", "metadata"),
        }

    environment = NightlyEnvironment(
        database=night["database"], bridge_directory=night["bridge"], clock=lambda: _et(TUESDAY, 17, 40),
        compare_decisions=compare, fence_store=SQLitePaperKillStore(),
    )
    assert run_replay_cli([], environment=environment) == 0

    assert compared and "orb" in compared[0]
    assert _fence_rows(fence_db) == {"paper:ORB_V2": (1, "sentinel: replay mismatch 2 decisions")}
    report = night["root"] / "reports" / "2026-10-12" / "evidence.json"
    assert json.loads(report.read_text())["decision_parity"] == {
        "status": "compared", "mismatches": {"ORB_V1": 1, "ORB_V2": 2}, "fenced": ["ORB_V2"],
        "already_fenced": [], "failed": {}, "note": "", "not_compared": {}, "would_fence": [],
    }

    # A restart inside the window finds the row already stopped: kept, not rewritten.
    with sqlite3.connect(fence_db) as connection:
        connection.execute("UPDATE autotrader_execution_fences SET reason='telegram /halt by owner'")
    assert run_replay_cli([], environment=environment) == 0
    assert _fence_rows(fence_db) == {"paper:ORB_V2": (1, "telegram /halt by owner")}
    parity = json.loads(report.read_text())["decision_parity"]
    assert (parity["fenced"], parity["already_fenced"]) == ([], ["ORB_V2"])
    # Never the global row.
    assert "paper" not in _fence_rows(fence_db)


def _decisions(*reasons: str):
    from trading.replay import ReplayEqualityReport, ReplayMismatch

    return ReplayEqualityReport(
        recorded_fingerprint="a" * 64,
        replayed_fingerprint="b" * 64,
        mismatches=tuple(
            ReplayMismatch(category="decisions", identity=f"candidate-{index}", reason=reason)
            for index, reason in enumerate(reasons)
        ),
    )


@pytest.mark.usefixtures("fences_live")
def test_a_decision_missing_from_the_replay_is_not_compared_and_never_fenced(night, fence_db):
    """N15a LOW-3: incomplete stored bars leave the replay without a recorded
    decision. That is missing data, never a mismatch: reported as not compared
    for its identity, and no identity is fenced for it."""
    from trading.research.nightly import decision_mismatches, decisions_not_compared
    from trading.storage.runtime_fence import SQLitePaperKillStore

    reports = {
        "ORB_V2": _decisions("missing_from_replay", "missing_from_replay"),
        "GAP_GO_V2": _decisions("fingerprint_mismatch", "missing_from_replay"),
        "VWAP_PB_V2": _decisions("unexpected_in_replay"),
    }
    assert decision_mismatches(reports) == {"GAP_GO_V2": 1, "VWAP_PB_V2": 1}
    assert decisions_not_compared(reports) == {"GAP_GO_V2": 1, "ORB_V2": 2}

    environment = NightlyEnvironment(
        database=night["database"], bridge_directory=night["bridge"], clock=lambda: _et(TUESDAY, 17, 40),
        compare_decisions=lambda _results: reports, fence_store=SQLitePaperKillStore(),
    )
    assert run_replay_cli([], environment=environment) == 0

    assert _fence_rows(fence_db) == {
        "paper:GAP_GO_V2": (1, "sentinel: replay mismatch 1 decisions"),
        "paper:VWAP_PB_V2": (1, "sentinel: replay mismatch 1 decisions"),
    }
    parity = json.loads((night["root"] / "reports" / "2026-10-12" / "evidence.json").read_text())["decision_parity"]
    assert parity["mismatches"] == {"GAP_GO_V2": 1, "VWAP_PB_V2": 1}
    assert parity["not_compared"] == {"GAP_GO_V2": 1, "ORB_V2": 2}

    # A night whose only gap is missing data: compared, nothing fenced, gap named.
    only_missing = {"ORB_V2": _decisions("missing_from_replay")}
    environment = NightlyEnvironment(
        database=night["database"], bridge_directory=night["bridge"], clock=lambda: _et(TUESDAY, 17, 40),
        compare_decisions=lambda _results: only_missing, fence_store=SQLitePaperKillStore(),
    )
    with sqlite3.connect(fence_db) as connection:
        connection.execute("DELETE FROM autotrader_execution_fences")
    assert run_replay_cli([], environment=environment) == 0
    parity = json.loads((night["root"] / "reports" / "2026-10-12" / "evidence.json").read_text())["decision_parity"]
    assert (parity["status"], parity["mismatches"], parity["not_compared"]) == ("compared", {}, {"ORB_V2": 1})
    assert _fence_rows(fence_db) == {}


@pytest.mark.usefixtures("fences_live")
def test_the_fence_a_parity_step_writes_is_the_one_the_promoter_reads(night, tmp_path, monkeypatch):
    """N15a LOW-4: one database, resolved by ``fence_database``. With the default
    fence store (production: no store injected), tonight's mismatch fence is the
    fence tonight's proposals show; a fence store on another file writes nothing."""
    from config import cfg
    from trading.research.nightly import PARITY_FENCE_DATABASE_MISMATCH, fence_database

    def compare(_results):
        return {"ORB_V2": _decisions("fingerprint_mismatch", "fingerprint_mismatch")}

    monkeypatch.setattr(cfg, "DB_PATH", str(night["database"]))  # the production layout: one file
    environment = NightlyEnvironment(
        database=night["database"], bridge_directory=night["bridge"], clock=lambda: _et(TUESDAY, 17, 40),
        compare_decisions=compare,
    )
    assert fence_database(environment) == Path(night["database"]).resolve()
    assert run_replay_cli([], environment=environment) == 0

    reports = night["root"] / "reports" / "2026-10-12"
    parity = json.loads((reports / "evidence.json").read_text())["decision_parity"]
    assert (parity["status"], parity["fenced"]) == ("compared", ["ORB_V2"])
    assert _fence_rows(night["database"])["paper:ORB_V2"] == (1, "sentinel: replay mismatch 2 decisions")
    proposals = {item["identity"]: item for item in json.loads((reports / "proposals.json").read_text())["identities"]}
    assert proposals["ORB_V2"]["fence"] == "sentinel: replay mismatch 2 decisions"
    assert proposals["VWAP_PB_V2"]["fence"] is None

    # The fence store on another file: the promoter would not see it, so nothing is written.
    from db.core import init_db
    import asyncio

    elsewhere = tmp_path / "elsewhere.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(elsewhere))
    asyncio.run(init_db())
    with sqlite3.connect(night["database"]) as connection:
        connection.execute("DELETE FROM autotrader_execution_fences")
    assert run_replay_cli([], environment=environment) == 0
    parity = json.loads((reports / "evidence.json").read_text())["decision_parity"]
    assert (parity["status"], parity["mismatches"], parity["fenced"]) == (
        PARITY_FENCE_DATABASE_MISMATCH, {"ORB_V2": 2}, [],
    )
    assert _fence_rows(elsewhere) == {} and _fence_rows(night["database"]) == {}


def test_a_failing_comparison_is_named_and_the_rest_of_the_night_still_counts(night, fence_db):
    def broken(_results):
        raise RuntimeError("bars missing")

    environment = NightlyEnvironment(
        database=night["database"], bridge_directory=night["bridge"], clock=lambda: _et(TUESDAY, 17, 40),
        compare_decisions=broken,
    )
    assert run_replay_cli([], notify="weekly", environment=environment) == 0

    evidence = json.loads((night["root"] / "reports" / "2026-10-12" / "evidence.json").read_text())
    assert evidence["decision_parity"]["status"] == "failed_runtimeerror"
    assert len(evidence["promotion"]) == 7
    assert _fence_rows(fence_db) == {}
