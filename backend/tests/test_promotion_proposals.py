"""The nightly promoter in propose mode (T-8, promotion Stage 1).

The rule is the owner's (OPERATOR_PROMPT Part 8.3): 30 trades, 20 sessions,
a positive bootstrap lower bound, slippage at most 0.1 R/trade, no unexplained
incident, one step at a time with 10 sessions at the current size. While the
open-risk term (v7c) is not deployed an eligible identity is HELD, never
proposed. Proposals are text and JSON only: nothing writes a constant, the
database or a fence.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from tests.test_live_ledger import _bracket, _save
from tests.test_nightly_evidence import TUESDAY, _environment, _et, night  # noqa: F401 - the fixture
from tests.test_setup_persistence import SESSION as MOMO_SESSION, _replay_momo_session
from trading.research import promotion_proposals as promoter
from trading.research.live_ledger import LiveBracket, LiveLedger, LiveTrade, read_live_ledger
from trading.research.paper_promotion import LiveEvidence, code_identity, read_live_evidence
from trading.research.promotion_proposals import (
    AT_CEILING,
    DEMOTION_PROPOSED,
    HELD,
    HELD_OPEN_RISK_TERM,
    NOT_ELIGIBLE,
    OPEN_RISK_TERM_DEPLOYED,
    PROPOSED,
    IdentityEvidence,
    PromotionProposals,
    ShadowOutcome,
    collect_proposals,
    evaluate_identity,
    identity_evidence,
    step_down,
    step_up,
)
from trading.research.replay import run_replay_cli
from trading.research.size_history import IdentitySizeHistory, SizeSession
import trading.risk.identity_sizing as identity_sizing

START = date(2026, 10, 19)


def _sessions(count: int, start: date = START) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _trade(day: date, decision_id: str, net: str, *, identity: str = "ORB_V2") -> LiveTrade:
    cost = Decimal("0.02")
    gross = Decimal(net) + cost
    return LiveTrade(
        session_date=day, decision_id=decision_id, strategy=identity, symbol="AAA", shares=100,
        risk_per_share=Decimal("0.50"), gross_pnl=gross * 50, commissions=Decimal("1.00"),
        gross_r=gross, cost_r=cost, exit_reason="target",
    )


def _history(identity: str, days: list[date], current: str, since: int) -> IdentitySizeHistory:
    before = [SizeSession(day, Decimal("1"), True, "recorded") for day in days[: len(days) - since]]
    after = [SizeSession(day, Decimal(current), True, "recorded") for day in days[len(days) - since :]]
    return IdentitySizeHistory(identity=identity, sessions=tuple(before + after))


def _evidence(
    per_day: list[list[str]],
    *,
    sessions: int = 22,
    current: str = "0.25",
    since: int = 12,
    slip: str | None = "0.03",
    incidents: tuple[str, ...] = (),
    identity: str = "ORB_V2",
) -> IdentityEvidence:
    days = _sessions(sessions)
    trades = tuple(
        _trade(day, f"d-{index}-{number}", net, identity=identity)
        for index, (day, nets) in enumerate(zip(days, per_day))
        for number, net in enumerate(nets)
    )
    shadow = {} if slip is None else {
        trade.decision_id: ShadowOutcome(True, trade.gross_r + Decimal(slip)) for trade in trades
    }
    return IdentityEvidence(
        identity=identity, current=Decimal(current), evidence_identity="e" * 64, window=tuple(days),
        trades=trades, shadow=shadow, incidents=incidents, first_real_order=days[0],
        probation_sessions=len(days), size_history=_history(identity, days, current, since),
    )


# 15 trading days of (+1.5R, -0.5R) inside 22 sessions: 30 trades, +0.50R each day on average.
STRONG = [["1.5", "-0.5"]] * 15


def test_the_rule_its_ladder_and_the_v7c_gate_are_pinned():
    assert (promoter.MIN_TRADES_PER_IDENTITY, promoter.MIN_SESSIONS, promoter.MIN_SESSIONS_SINCE_STEP) == (30, 20, 10)
    assert promoter.MAX_SLIPPAGE_R_PER_TRADE == Decimal("0.1") and promoter.MAX_UNEXPLAINED_INCIDENTS == 0
    assert promoter.SIZE_LADDER == (Decimal("0.25"), Decimal("0.5"), Decimal("1"))
    assert (promoter.PROBATION_SESSIONS, promoter.DEMOTION_MIN_TRADES) == (5, 10)
    assert promoter.MODE == "propose" and promoter.RULE_VERSION == "promotion-proposals-v1"
    # Owner decision 2026-10-15: no step above current sizes before v7c is deployed.
    assert OPEN_RISK_TERM_DEPLOYED is False


def test_eligible_evidence_is_held_while_the_open_risk_term_is_not_deployed():
    report = evaluate_identity(_evidence(STRONG))  # the module's own gate
    assert (report.status, report.proposed, report.reasons, report.token) == (
        HELD, Decimal("0.5"), (), HELD_OPEN_RISK_TERM,
    )
    assert report.proposal_line() == "HELD ORB_V2: eligible for ×0.5 but v7c (open-risk term) is not deployed"
    assert report.status_line() == "ORB_V2 ×0.25: probation 5/5; held (v7c)"
    payload = report.payload()
    assert (payload["status"], payload["token"], payload["current"], payload["proposed"]) == (
        "held", "held_open_risk_term_not_deployed", "0.25", "0.5",
    )


def test_once_the_gate_opens_eligible_evidence_is_a_proposal_with_its_numbers():
    report = evaluate_identity(_evidence(STRONG), open_risk_term_deployed=True)
    assert (report.status, report.proposed) == (PROPOSED, Decimal("0.5"))
    assert report.proposal_line() == (
        "PROPOSAL ORB_V2: ×0.25 → ×0.5; 30 trades, expectancy +0.50R (lower bound +0.50R),"
        " slippage 0.03R/trade, sessions 22 (12 since the last step)"
    )
    evidence = report.payload()["evidence"]
    assert (evidence["trades"], evidence["sessions"], evidence["slippage_r_per_trade"]) == (30, 22, "0.0300")
    assert (evidence["expectancy_r"], evidence["expectancy_lower_bound_r"], evidence["sessions_since_last_step"]) == (
        "0.5000", "0.5000", 12,
    )
    assert report.payload()["window"]["sessions"] == 22


@pytest.mark.parametrize(
    ("change", "reason", "text"),
    [
        (dict(per_day=[*STRONG[:-1], ["1.5"]]), "trades_below_minimum", "trades 29/30"),
        (dict(sessions=19), "sessions_below_minimum", "sessions 19/20"),
        (dict(per_day=[["0.5", "-0.5"]] * 15), "expectancy_lower_bound_not_positive", "expectancy lower bound +0.00R"),
        (dict(slip="0.11"), "slippage_above_maximum", "slippage 0.11R/trade > 0.10R/trade"),
        (dict(slip=None), "slippage_unmeasured", "slippage unmeasured"),
        (dict(incidents=("2026-10-20:d-9:needs_attention",)), "unexplained_incidents", "1 unexplained incident(s)"),
        (dict(since=9), "sessions_since_step_below_minimum", "9/10 sessions since the last step"),
    ],
)
def test_each_threshold_alone_withholds_the_proposal(change, reason, text):
    options = {"per_day": STRONG, **change}
    report = evaluate_identity(_evidence(**options), open_risk_term_deployed=True)
    assert (report.status, report.reasons, report.proposed) == (NOT_ELIGIBLE, (reason,), None)
    assert report.proposal_line() is None
    probation = f"probation {min(len(report.window), 5)}/5"
    assert report.status_line() == f"ORB_V2 ×0.25: {probation}; not eligible: {text}"


@pytest.mark.parametrize(
    "change", [dict(slip="0.1"), dict(since=10), dict(sessions=20)]
)
def test_the_thresholds_themselves_pass(change):
    report = evaluate_identity(_evidence(**{"per_day": STRONG, **change}), open_risk_term_deployed=True)
    assert report.status == PROPOSED


@pytest.mark.parametrize(
    ("current", "proposed"), [("0.25", "0.5"), ("0.5", "1")]
)
def test_one_step_at_a_time(current, proposed):
    report = evaluate_identity(_evidence(STRONG, current=current), open_risk_term_deployed=True)
    assert (report.status, report.proposed) == (PROPOSED, Decimal(proposed))
    assert step_up(Decimal(current)) == Decimal(proposed)


def test_the_ladder_ends_at_x1_and_has_no_other_rungs():
    assert [step_up(Decimal(value)) for value in ("0.25", "0.5", "1", "0.3", "2")] == [
        Decimal("0.5"), Decimal("1"), None, None, None,
    ]
    assert [step_down(Decimal(value)) for value in ("1", "0.5", "0.25")] == [Decimal("0.5"), Decimal("0.25"), None]
    ceiling = evaluate_identity(_evidence(STRONG, current="1", identity="MOMO_HOD_V2"), open_risk_term_deployed=True)
    assert (ceiling.status, ceiling.proposed, ceiling.proposal_line()) == (AT_CEILING, None, None)
    assert ceiling.status_line() == "MOMO_HOD_V2 ×1: probation 5/5; at the ×1 ceiling"
    off = evaluate_identity(_evidence(STRONG, current="0.3"), open_risk_term_deployed=True)
    assert (off.status, off.proposed) == (NOT_ELIGIBLE, None)
    assert "current_not_on_ladder" in off.reasons


@pytest.mark.parametrize("gate", [False, True])
def test_negative_evidence_proposes_a_demotion_whatever_the_gate(gate):
    losing = [["-1", "0.2"]] * 12
    report = evaluate_identity(
        _evidence(losing, sessions=12, current="0.5", identity="VWAP_PB_V2"), open_risk_term_deployed=gate
    )
    assert (report.status, report.proposed) == (DEMOTION_PROPOSED, Decimal("0.25"))
    assert report.proposal_line() == (
        "DEMOTION PROPOSAL VWAP_PB_V2: ×0.5 → ×0.25; 24 trades, expectancy -0.40R (upper bound -0.40R),"
        " slippage 0.03R/trade, sessions 12"
    )
    bottom = evaluate_identity(_evidence(losing, sessions=12, current="0.25"), open_risk_term_deployed=gate)
    assert (bottom.status, bottom.proposed) == (DEMOTION_PROPOSED, None)
    assert bottom.proposal_line().startswith("DEMOTION PROPOSAL ORB_V2: ×0.25 → shadow-only; 24 trades")
    assert bottom.payload()["proposed_text"] == "shadow-only"
    # Too few trades to call it negative: no demotion, just not eligible.
    few = evaluate_identity(_evidence([["-1", "0.2"]] * 4, sessions=12, current="0.5"))
    assert few.status == NOT_ELIGIBLE


def test_the_window_runs_through_a_size_step_and_counts_incidents_and_probation():
    days = _sessions(8)
    fingerprint = code_identity("orb-v2").fingerprint()
    old = date(2026, 10, 16)  # a session under an older evidence identity: not counted
    trades = (
        _trade(old, "d-old", "1"),
        _trade(days[0], "d-before", "1"),
        _trade(days[1], "d-x1", "0.5"),
        _trade(days[4], "d-q", "-0.5"),
        _trade(days[2], "d-momo", "3", identity="MOMO_HOD_V2"),
    )
    ledger = LiveLedger(
        trades=trades,
        sessions=(old, *days),
        brackets=(
            LiveBracket(old, "d-old", "ORB_V2", "closed"),
            LiveBracket(days[0], "d-before", "ORB_V2", "closed"),
            LiveBracket(days[1], "d-x1", "ORB_V2", "closed"),
            LiveBracket(days[3], "d-att", "ORB_V2", "needs_attention"),
            LiveBracket(days[4], "d-q", "ORB_V2", "closed"),
            LiveBracket(days[5], "d-other", "GAP_GO_V2", "needs_attention"),
        ),
        unreadable=(f"{days[6].isoformat()}.json", f"{days[7].isoformat()}.json:d-zz"),
    )
    live = LiveEvidence(sessions={fingerprint: frozenset(days), "f" * 64: frozenset({old})})
    evidence = identity_evidence(
        "ORB_V2", current=Decimal("0.25"), live=live, ledger=ledger,
        shadow={"d-x1": ShadowOutcome(True, Decimal("0.6")), "d-old": ShadowOutcome(True, Decimal("9"))},
        size_history=IdentitySizeHistory(identity="ORB_V2"),
        multipliers={"d-before": "1", "d-x1": "1", "d-q": "0.25"},
    )
    # The first real order was on the older identity's session; the window is the current identity's.
    assert evidence.first_real_order == old and evidence.window == tuple(days)
    assert [trade.decision_id for trade in evidence.trades] == ["d-before", "d-x1", "d-q"]
    # One evidence across the step: x1 trades and x0.25 trades in one window.
    assert evidence.trades_by_multiplier == {"0.25": 1, "1": 2}
    assert evidence.incidents == (
        f"{days[3].isoformat()}:d-att:needs_attention",
        f"{days[6].isoformat()}.json:file_unreadable",
    )
    assert set(evidence.shadow) == {"d-x1"}
    assert evidence.probation_sessions == 9
    report = evaluate_identity(evidence)
    assert report.slippage.pairs == 1 and report.slippage.mean_r == Decimal("0.6") - Decimal("0.52")
    none_yet = identity_evidence(
        "GAP_GO_V2", current=Decimal("0.5"), live=live, ledger=replace(ledger, brackets=()), shadow={},
        size_history=IdentitySizeHistory(identity="GAP_GO_V2"),
    )
    assert none_yet.first_real_order is None and none_yet.window == () and none_yet.probation_sessions == 0
    assert evaluate_identity(none_yet).status_line() == (
        "GAP_GO_V2 ×0.5: probation 0/5 (no real order yet); not eligible: trades 0/30, sessions 0/20,"
        " slippage unmeasured, 0/10 sessions since the last step"
    )


def test_the_text_section_lists_proposals_first_then_every_identity():
    reports = (
        evaluate_identity(_evidence(STRONG, current="1", identity="MOMO_HOD_V2")),
        evaluate_identity(_evidence(STRONG)),
        evaluate_identity(_evidence([["-1", "0.2"]] * 12, sessions=12, current="0.5", identity="VWAP_PB_V2")),
        evaluate_identity(_evidence([["1"]] * 3, sessions=3, current="0.5", identity="GAP_GO_V2")),
    )
    proposals = PromotionProposals(as_of=START, identities=reports)
    assert proposals.lines() == [
        "Promotion proposals (promotion-proposals-v1, propose mode: nothing changes until the owner approves)",
        "HELD ORB_V2: eligible for ×0.5 but v7c (open-risk term) is not deployed",
        "DEMOTION PROPOSAL VWAP_PB_V2: ×0.5 → ×0.25; 24 trades, expectancy -0.40R (upper bound -0.40R),"
        " slippage 0.03R/trade, sessions 12",
        "MOMO_HOD_V2 ×1: probation 5/5; at the ×1 ceiling",
        "ORB_V2 ×0.25: probation 5/5; held (v7c)",
        "VWAP_PB_V2 ×0.5: probation 5/5; demotion proposed",
        "GAP_GO_V2 ×0.5: probation 3/5; not eligible: trades 3/30, sessions 3/20, 3/10 sessions since the last step",
    ]
    payload = proposals.payload(generated_at=datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc))
    assert (payload["schema"], payload["mode"], payload["open_risk_term_deployed"]) == (
        "autotrader-v1/promotion-proposals", "propose", False,
    )
    assert payload["grants"].startswith("nothing: a proposal changes no constant, database row or fence")
    assert [item["identity"] for item in payload["identities"]] == ["MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2"]
    assert payload["lines"] == proposals.lines()
    json.dumps(payload)  # plain JSON
    assert PromotionProposals(as_of=START, readable=False, note="failed_oserror").lines()[1:] == [
        "not evaluated: failed_oserror"
    ]


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


@pytest.mark.anyio
async def test_the_promoter_reads_the_ledger_the_sidecar_and_the_bridge_and_writes_nothing(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    database = tmp_path / "autotrader.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    decisions, _ = await _replay_momo_session(database, monkeypatch)
    bf_b = decisions["BF B"]
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO autotrader_manager_signal_outcomes
               (run_id, setup_fingerprint, fill_model_id, simulated_at, filled, exit_reason, r_multiple, cost_r)
               VALUES (?, ?, 'replay-bracket-v1', '2026-10-12T22:00:00+00:00', 1, 'target', '2.0000', '0.0400')""",
            (bf_b.run_id, bf_b.setup_fingerprint),
        )
    bridge = tmp_path / "bridge"
    _save(bridge, MOMO_SESSION, [
        _bracket(bf_b.id, status="closed", filled=100, avg_entry="26.7700", exit_filled=100,
                 avg_exit="27.2500", exit_reason="target"),
    ])
    sizing = tmp_path / "autotrader-decision-sizing"
    sizing_source = Path(identity_sizing.__file__)
    before = (_digest(database), _digest(sizing_source), sorted(p.name for p in sizing.iterdir()),
              dict(identity_sizing.IDENTITY_SIZE_MULTIPLIER))

    proposals = collect_proposals(
        database=database, ledger=read_live_ledger(bridge), live=read_live_evidence(database), as_of=MOMO_SESSION,
    )

    after = (_digest(database), _digest(sizing_source), sorted(p.name for p in sizing.iterdir()),
             dict(identity_sizing.IDENTITY_SIZE_MULTIPLIER))
    assert after == before  # no constant, no row, no sidecar written
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM autotrader_execution_fences").fetchone() == (0,)
    momo = {item.identity: item for item in proposals.identities}["MOMO_HOD_V2"]
    assert [item.identity for item in proposals.identities] == ["MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2"]
    assert (momo.status, momo.trades, momo.sessions, momo.probation_sessions) == (AT_CEILING, 1, 1, 1)
    assert momo.window == (MOMO_SESSION,) and momo.evidence_identity == code_identity("momo").fingerprint()
    # Modelled +2.0R against the real +1.85R (0.48 / 0.26): 0.15R of slippage on one pair.
    assert (momo.slippage.pairs, momo.slippage.mean_r) == (1, Decimal("2.0000") - Decimal("1.8462"))
    assert momo.trades_by_multiplier == {"1": 1}  # recorded by the pipeline's sidecar
    steps = proposals.size_steps.payload()["identities"]["MOMO_HOD_V2"]
    assert steps["events"] == [{
        "session_date": MOMO_SESSION.isoformat(), "event": "became_sendable", "from": None, "to": "1",
        "source": "recorded",
    }]
    assert proposals.lines()[-4] == "MOMO_HOD_V2 ×1: probation 1/5; at the ×1 ceiling"


def test_the_nightly_job_writes_proposals_and_the_weekly_text_carries_them(night):  # noqa: F811
    assert run_replay_cli([], notify="weekly", environment=_environment(night, _et(TUESDAY, 17, 40))) == 0
    (summary,) = night["sent"]
    evidence = summary.split("\n\n")[0].splitlines()
    header = evidence.index(
        "Promotion proposals (promotion-proposals-v1, propose mode: nothing changes until the owner approves)"
    )
    assert evidence[header + 1 :] == [
        "MOMO_HOD_V2 ×1: probation 2/5; at the ×1 ceiling",
        "ORB_V2 ×0.25: probation 0/5 (no real order yet); not eligible: trades 0/30, sessions 0/20,"
        " slippage unmeasured, 0/10 sessions since the last step",
        "VWAP_PB_V2 ×0.5: probation 0/5 (no real order yet); not eligible: trades 0/30, sessions 0/20,"
        " slippage unmeasured, 0/10 sessions since the last step",
        "GAP_GO_V2 ×0.5: probation 0/5 (no real order yet); not eligible: trades 0/30, sessions 0/20,"
        " slippage unmeasured, 0/10 sessions since the last step",
    ]
    directory = night["root"] / "reports" / "2026-10-12"
    document = json.loads((directory / "proposals.json").read_text())
    assert (document["mode"], document["open_risk_term_deployed"], document["rule"]) == (
        "propose", False, "promotion-proposals-v1",
    )
    assert [item["status"] for item in document["identities"]] == [
        "at_ceiling", "not_eligible", "not_eligible", "not_eligible",
    ]
    assert (directory / "proposals.json").stat().st_mode & 0o777 == 0o600
    size_steps = json.loads((directory / "evidence.json").read_text())["size_steps"]
    # The fixture's runs carry a sha the commit table does not list: unknown, named, never guessed.
    assert size_steps["identities"]["VWAP_PB_V2"]["unknown_sources"] == ["abcdef1"]


def test_the_promoter_can_only_propose():
    source = Path(promoter.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden = (
        "trading.authority",
        "trading.execution",
        "trading.evidence",
        "trading.storage.repository",
        "trading.storage.runtime_fence",
        "trading.operations",
        "db.core",
        "aiosqlite",
        "os",
        "subprocess",
    )
    assert not [name for name in imported if name.startswith(forbidden)], imported
    for statement in ("INSERT", "UPDATE ", "DELETE", "CREATE ", "DROP ", "mode=rw", "write_text", "write_bytes", "open("):
        assert statement not in source, statement
    assert "?mode=ro" in source


# --- P1-B: a fenced identity is HELD, never proposed ------------------------------------

SENTINEL_REASON = "sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)"


@pytest.mark.parametrize("gate", [False, True])
def test_an_eligible_fenced_identity_is_held_never_proposed_whatever_the_gate(gate):
    fenced = evaluate_identity(replace(_evidence(STRONG), fence=SENTINEL_REASON), open_risk_term_deployed=gate)
    assert (fenced.status, fenced.proposed, fenced.reasons, fenced.token) == (
        HELD, Decimal("0.5"), (), promoter.HELD_IDENTITY_FENCED,
    )
    assert fenced.proposal_line() == f"HELD ORB_V2: eligible for ×0.5 but its fence is stopped ({SENTINEL_REASON})"
    assert fenced.status_line() == "ORB_V2 ×0.25: probation 5/5; held (fenced)"
    payload = fenced.payload()
    assert (payload["status"], payload["token"], payload["fence"]) == ("held", "held_identity_fenced", SENTINEL_REASON)
    # The same evidence unfenced: a proposal once the gate is open (the control).
    unfenced = evaluate_identity(_evidence(STRONG), open_risk_term_deployed=gate)
    assert unfenced.status == (PROPOSED if gate else HELD) and unfenced.payload()["fence"] is None


def test_a_fence_never_holds_back_a_demotion_and_is_named_on_every_status_line():
    losing = [["-1", "0.2"]] * 12
    demoted = evaluate_identity(
        replace(_evidence(losing, sessions=12, current="0.5", identity="VWAP_PB_V2"), fence="telegram /halt by owner"),
        open_risk_term_deployed=True,
    )
    assert (demoted.status, demoted.proposed) == (DEMOTION_PROPOSED, Decimal("0.25"))
    assert demoted.status_line() == "VWAP_PB_V2 ×0.5: probation 5/5; demotion proposed; fenced (telegram /halt by owner)"
    thin = evaluate_identity(replace(_evidence([["1"]] * 3, sessions=3), fence="telegram /halt by owner"))
    assert thin.status == NOT_ELIGIBLE and thin.status_line().endswith("; fenced (telegram /halt by owner)")


def test_the_fence_names_are_the_runtime_fence_stores():
    import inspect

    from trading.storage import runtime_fence

    assert (promoter.GLOBAL_FENCE, promoter.IDENTITY_FENCE_PREFIX) == (
        runtime_fence.GLOBAL_FENCE, runtime_fence.IDENTITY_FENCE_PREFIX,
    )
    assert f"FROM {promoter.FENCE_TABLE}" in inspect.getsource(runtime_fence.SQLitePaperKillStore)


@pytest.mark.anyio
async def test_the_promoter_reads_the_fence_table_read_only_and_holds_a_fenced_identity(tmp_path, monkeypatch):
    """The real store's rows, read by the nightly call: ORB_V2 stopped by the Sentinel,
    VWAP_PB_V2 stopped then resumed. Eligible evidence for every identity (the rule's
    numbers are the pure tests'): ORB_V2 is HELD (fenced), the others are proposals
    once the gate is open. Then the global stop holds every identity."""
    from config import cfg
    from db.core import init_db
    from trading.storage.runtime_fence import SQLitePaperKillStore

    database = tmp_path / "autotrader.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    await init_db()
    store = SQLitePaperKillStore()
    await store.stop(reason=SENTINEL_REASON, identity="ORB_V2")
    await store.stop(reason="telegram /halt by owner", identity="VWAP_PB_V2")
    await store.resume(reason="owner: checked", identity="VWAP_PB_V2")
    before = _digest(database)

    assert promoter.read_fences(database) == {"ORB_V2": SENTINEL_REASON}
    assert _digest(database) == before  # read-only

    def eligible(name, *, current, fence=None, **_rest):
        return replace(_evidence(STRONG, identity=name, current=str(current)), fence=fence)

    monkeypatch.setattr(promoter, "identity_evidence", eligible)
    sizes = {"MOMO_HOD_V2": Decimal("0.5"), "ORB_V2": Decimal("0.25"), "VWAP_PB_V2": Decimal("0.25"),
             "GAP_GO_V2": Decimal("0.5")}
    proposals = collect_proposals(
        database=database, ledger=LiveLedger(), live=LiveEvidence(), as_of=START, current=sizes,
        open_risk_term_deployed=True,
    )
    statuses = {item.identity: (item.status, item.token, item.fence) for item in proposals.identities}
    assert statuses == {
        "MOMO_HOD_V2": (PROPOSED, None, None),
        "ORB_V2": (HELD, promoter.HELD_IDENTITY_FENCED, SENTINEL_REASON),
        "VWAP_PB_V2": (PROPOSED, None, None),
        "GAP_GO_V2": (PROPOSED, None, None),
    }
    assert f"HELD ORB_V2: eligible for ×0.5 but its fence is stopped ({SENTINEL_REASON})" in proposals.lines()
    assert not any(line.startswith("PROPOSAL ORB_V2") for line in proposals.lines())

    await store.stop(reason="telegram /stop by owner")  # the global kill switch
    held = collect_proposals(
        database=database, ledger=LiveLedger(), live=LiveEvidence(), as_of=START, current=sizes,
        open_risk_term_deployed=True,
    )
    assert {item.identity: item.fence for item in held.identities} == {
        "MOMO_HOD_V2": "global PAPER stop: telegram /stop by owner",
        "ORB_V2": SENTINEL_REASON,
        "VWAP_PB_V2": "global PAPER stop: telegram /stop by owner",
        "GAP_GO_V2": "global PAPER stop: telegram /stop by owner",
    }
    assert {item.status for item in held.identities} == {HELD}
    assert not any(line.startswith("PROPOSAL ") for line in held.lines())


def test_an_unreadable_fence_table_proposes_nothing(tmp_path):
    database = tmp_path / "broken.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE autotrader_execution_fences (name TEXT PRIMARY KEY)")  # no stopped column
    with pytest.raises(sqlite3.Error):
        promoter.read_fences(database)
    proposals = collect_proposals(database=database, ledger=LiveLedger(), live=LiveEvidence(), as_of=START)
    assert not proposals.readable and proposals.note == "OperationalError"
    assert not any(line.startswith("PROPOSAL ") for line in proposals.lines())
    # No database, or a schema before the fence table: nothing is fenced.
    assert promoter.read_fences(tmp_path / "missing.db") == {}
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert promoter.read_fences(empty) == {}
