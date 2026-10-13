"""The paper promotion rule: thresholds pinned, day-clustered bootstrap, reports only."""
from __future__ import annotations

import ast
import asyncio
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from tests.test_research import SESSION, _capture_store
from tests.test_signal_outcomes import _phase1_database, _run, _setup, _strategy
from trading.contracts import RiskPolicy, ShadowRuntimeConfig
from trading.operations import shadow_service
from trading.orchestration.session_runner import orb_strategy_spec
from trading.research import paper_promotion
from trading.research.outcomes import track_outcomes
from trading.research.paper_promotion import (
    AI_ACTIVE,
    AI_ADVISORY,
    AI_INSUFFICIENT,
    ELIGIBLE,
    NOT_ELIGIBLE,
    WINDOW_IN_RULES,
    EvidenceIdentity,
    EvidenceTrade,
    ai_do_no_harm,
    code_identity,
    day_clustered_bootstrap,
    evaluate_promotion,
    evaluate_registry,
    identity_from_run,
    paired_difference_bootstrap,
    read_live_evidence,
)
from trading.research.replay import replay_strategy

UTC = timezone.utc
START = date(2026, 9, 1)
IDENTITY = EvidenceIdentity(strategy="VWAP_PB_V2", rules="r" * 64, entry_window="w" * 64, risk_policy="p" * 64)


def _day(index: int) -> date:
    return START + timedelta(days=index)


def _trades(per_day: list[list[str]], *, selected: list[list[bool]] | None = None) -> list[EvidenceTrade]:
    trades = []
    for index, values in enumerate(per_day):
        for position, value in enumerate(values):
            day = _day(index)
            trades.append(
                EvidenceTrade(
                    session_date=day,
                    exit_at=datetime.combine(day, time(15, position), tzinfo=UTC),
                    net_r=Decimal(value),
                    selected=bool(selected and selected[index][position]),
                )
            )
    return trades


def _strong(days: int, *, per_day: list[str]) -> list[EvidenceTrade]:
    return _trades([list(per_day) for _ in range(days)])


# --- the rule is fixed before any data is seen --------------------------------------


def test_the_rule_and_its_thresholds_are_pinned():
    assert paper_promotion.RULE_VERSION == "paper-promotion-v1"
    assert paper_promotion.thresholds() == {
        "min_sessions": "20",
        "min_replay_trades": "100",
        "min_live_setups": "30",
        "bootstrap_confidence_one_sided": "0.90",
        "bootstrap_resamples": "10000",
        "min_profit_factor": "1.2",
        "max_replay_drawdown_r": "15",
        "min_managed_setups": "10",
    }
    # Nearest-rank one-sided 90% bounds over 10 000 resamples.
    assert paper_promotion._ranks(10_000, Decimal("0.90")) == (999, 8999)


def test_strong_evidence_is_eligible_and_the_report_grants_nothing():
    replay = _strong(25, per_day=["2", "2", "2", "-1", "-1"])  # 125 trades, +0.8R each on average
    live = _strong(25, per_day=["2", "-1"])  # 50 setups, +0.5R

    report = evaluate_promotion(
        identity=IDENTITY, registry_key="vwap-v2", live=live, live_sessions=25,
        replay=replay, replay_sessions=25,
    )

    assert (report.status, report.reasons) == (ELIGIBLE, ())
    assert report.replay is not None
    assert (report.replay.trades, report.live.trades) == (125, 50)
    # Every day has the same mean, so every resample has it too.
    assert report.replay.bound.lower == report.replay.bound.upper == Decimal("0.8")
    assert report.live.bound.lower == Decimal("0.5")
    assert report.replay.profit_factor == Decimal("3")
    assert report.replay.max_drawdown_r == Decimal("2")
    payload = report.payload()
    assert payload["grants"] == "nothing: eligibility is evidence for the owner's review only"
    assert payload["status"] == "ELIGIBLE" and payload["identity"]["fingerprint"] == IDENTITY.fingerprint()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"live_sessions": 19}, "live_sessions_below_minimum"),
        ({"replay_sessions": 19}, "replay_sessions_below_minimum"),
        ({"replay": _strong(19, per_day=["2", "2", "2", "-1", "-1"])[:99]}, "replay_trades_below_minimum"),
        ({"live": _strong(14, per_day=["2", "-1"])[:29]}, "live_setups_below_minimum"),
        ({"live": _strong(25, per_day=["1", "-1.2"])}, "live_expectancy_lower_bound_not_positive"),
        ({"replay": _strong(25, per_day=["1", "1", "-1", "-1", "-0.1"])}, "replay_expectancy_lower_bound_not_positive"),
        # +0.05R a trade, but wins only 1.1x the losses.
        ({"live": _strong(25, per_day=["1.1", "1.1", "1.1", "-1", "-1", "-1"] * 1)}, "live_profit_factor_below_minimum"),
        ({"replay": _strong(25, per_day=["1.1", "-1", "1.1", "-1", "1.1", "-1"])}, "replay_profit_factor_below_minimum"),
        ({"replay": None}, "replay_unavailable"),
    ],
)
def test_each_threshold_alone_makes_a_strategy_not_eligible(change, reason):
    values = {
        "identity": IDENTITY,
        "registry_key": "vwap-v2",
        "live": _strong(25, per_day=["2", "-1"]),
        "live_sessions": 25,
        "replay": _strong(25, per_day=["2", "2", "2", "-1", "-1"]),
        "replay_sessions": 25,
    }
    values.update(change)

    report = evaluate_promotion(**values)

    assert report.status == NOT_ELIGIBLE
    assert reason in report.reasons


def test_a_deep_replay_drawdown_alone_is_disqualifying():
    # 30 days of +0.8R, then one day losing 16R in a row, then recovery: from
    # the last peak (each day ends -1, -1 after its +6R) the curve falls 18R.
    days = [["2", "2", "2", "-1", "-1"] for _ in range(30)] + [["-1"] * 16] + [["2"] * 10]
    report = evaluate_promotion(
        identity=IDENTITY, registry_key="vwap-v2", live=_strong(25, per_day=["2", "-1"]),
        live_sessions=25, replay=_trades(days), replay_sessions=32,
    )
    assert report.replay is not None and report.replay.max_drawdown_r == Decimal("18")
    assert report.reasons == ("replay_drawdown_above_maximum",)


# --- the bootstrap --------------------------------------------------------------------


def test_the_bootstrap_resamples_whole_days():
    # Day A: two +1R trades; day B: one -1R. Two-day resamples: AA = +1, AB/BA = +1/3, BB = -1.
    bound = day_clustered_bootstrap(_trades([["1", "1"], ["-1"]]), seed_label="two-days")
    assert bound.estimate == Decimal("1") / 3
    assert (bound.lower, bound.upper) == (Decimal("-1"), Decimal("1"))
    assert (bound.days, bound.trades, bound.resamples) == (2, 3, 10_000)
    # Ten winners on one day and ten losers on another move together: a
    # trade-level bootstrap would bound the mean near +-0.3R, a day-level one at +-1R.
    clustered = day_clustered_bootstrap(_trades([["1"] * 10, ["-1"] * 10]), seed_label="clusters")
    assert (clustered.lower, clustered.upper) == (Decimal("-1"), Decimal("1"))


def test_the_bootstrap_is_reproducible_and_seeded_by_identity_and_sample():
    trades = _trades([["1", "-0.5"], ["2"], ["-1", "-1", "0.3"], ["0.5"], ["-0.2", "1.4"]])
    first = day_clustered_bootstrap(trades, seed_label="a:live")
    assert day_clustered_bootstrap(trades, seed_label="a:live") == first
    other = day_clustered_bootstrap(trades, seed_label="b:live")
    assert other.estimate == first.estimate and (other.lower, other.upper) != (first.lower, first.upper)
    assert first.lower is not None and first.upper is not None and first.lower < first.estimate < first.upper
    # One day cannot be resampled: no bound, so nothing can pass on it.
    single = day_clustered_bootstrap(_trades([["1", "2"]]), seed_label="x")
    assert (single.lower, single.upper, single.resamples) == (None, None, 0)
    assert day_clustered_bootstrap([], seed_label="x").estimate is None


# --- AI do-no-harm ---------------------------------------------------------------------


def _paired(days: int, *, chosen: str, other: str) -> list[EvidenceTrade]:
    values = [[chosen, other, other] for _ in range(days)]
    picks = [[True, False, False] for _ in range(days)]
    return _trades(values, selected=picks)


def test_an_ai_selection_that_costs_r_is_advisory_one_that_helps_stays_active():
    harmful = ai_do_no_harm(_paired(15, chosen="-1", other="1"), identity=IDENTITY)
    assert harmful.status == AI_ADVISORY
    assert harmful.difference.upper is not None and harmful.difference.upper < 0
    helpful = ai_do_no_harm(_paired(15, chosen="2", other="-1"), identity=IDENTITY)
    assert helpful.status == AI_ACTIVE
    # Too few selected setups (9 < 10): no verdict either way.
    few = ai_do_no_harm(_paired(9, chosen="-1", other="1") + _strong(5, per_day=["1"]), identity=IDENTITY)
    assert few.status == AI_INSUFFICIENT and few.selected_trades == 9


def test_the_weekly_lines_name_an_eligible_strategy_and_an_advisory_ai():
    from trading.research.report_notify import evidence_lines

    # Every day: the AI picked the -1R trade and passed on two +2R trades.
    live = _trades([["-1", "2", "2"] for _ in range(25)], selected=[[True, False, False]] * 25)
    report = evaluate_promotion(
        identity=IDENTITY, registry_key="vwap-v2", live=live, live_sessions=25,
        replay=_strong(25, per_day=["2", "2", "2", "-1", "-1"]), replay_sessions=25,
    )

    assert report.eligible and report.ai.status == AI_ADVISORY
    assert evidence_lines([report]) == [
        "Paper promotion (paper-promotion-v1; reports only, nothing is granted)",
        "VWAP_PB_V2: ELIGIBLE for the owner's review; AI advisory (its picks cost R)"
        " (picks - all -2.00R, upper bound -2.00R)",
    ]


def test_the_paired_difference_compares_on_the_same_days():
    trades = _paired(12, chosen="-1", other="1")
    bound = paired_difference_bootstrap(trades, seed_label="paired")
    # Every day: selected -1, all (-1 + 1 + 1) / 3: a constant -4/3 difference.
    assert bound.estimate == bound.lower == bound.upper == Decimal("-4") / 3


# --- the evidence identity -------------------------------------------------------------


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


@pytest.mark.parametrize("key", list(shadow_service.STRATEGIES))
def test_live_runs_and_the_replay_share_the_code_identity(key):
    evaluator_factory, spec_factory, prompt_id = shadow_service.STRATEGIES[key]
    prints = shadow_service.policy_fingerprints(
        _config(), RiskPolicy(), prompt_id=prompt_id, strategy_name=key
    )
    from_run = identity_from_run(spec_factory().name, evaluator_factory().strategy_kind, prints)
    assert from_run == code_identity(key)


def test_any_change_to_rules_window_risk_or_manager_is_a_new_identity():
    vwap1, vwap2 = code_identity("vwap"), code_identity("vwap-v2")
    assert vwap1.entry_window == WINDOW_IN_RULES and vwap2.entry_window != WINDOW_IN_RULES
    assert vwap1.fingerprint() != vwap2.fingerprint()
    base = code_identity("orb-v2")
    variants = [
        code_identity("orb-v2", risk_policy=RiskPolicy(max_open_positions=2)),
        EvidenceIdentity(**{**_fields(base), "rules": "x" * 64}),
        EvidenceIdentity(**{**_fields(base), "entry_window": "y" * 64}),
        EvidenceIdentity(**{**_fields(base), "manager_version": "manager-v1"}),
        EvidenceIdentity(**{**_fields(base), "prompt_id": "day-plan-v1"}),
    ]
    assert len({base.fingerprint(), *(variant.fingerprint() for variant in variants)}) == 6
    assert identity_from_run("ORB_V2", "orb", {"rules": "r" * 64}) is None  # no risk policy


def _fields(identity: EvidenceIdentity) -> dict[str, object]:
    return {
        "strategy": identity.strategy,
        "rules": identity.rules,
        "entry_window": identity.entry_window,
        "risk_policy": identity.risk_policy,
        "manager_version": identity.manager_version,
        "prompt_id": identity.prompt_id,
    }


# --- reading live evidence from the ledger ---------------------------------------------


def _orb_prints(**overrides: str) -> dict[str, str]:
    prints = shadow_service.policy_fingerprints(_config(), RiskPolicy(), strategy_name="orb")
    prints.update(overrides)
    return prints


def test_live_evidence_counts_only_the_current_identity_and_reads_the_ai_selection(tmp_path):
    path = _phase1_database(tmp_path / "autotrader.db")
    orb = orb_strategy_spec()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _strategy(connection, "strategy-orb", orb)
        _run(connection, "run-orb", "strategy-orb", SESSION, prints=_orb_prints())
        # An older run under another risk policy: its own identity, not counted.
        _run(connection, "run-old", "strategy-orb", date(2026, 10, 9), prints=_orb_prints(risk="0" * 64))
        _setup(connection, run_id="run-orb", strategy_id="strategy-orb", spec=orb, kind="orb",
               fingerprint="a" * 64, con_id=4931, symbol="BF B", session_date=SESSION,
               signal_end="14:00", entry="26.6", stop="26.51", target="26.78")
        _setup(connection, run_id="run-orb", strategy_id="strategy-orb", spec=orb, kind="orb",
               fingerprint="b" * 64, con_id=493581450, symbol="FIGS", session_date=SESSION,
               signal_end="14:15", entry="16.93", stop="16.77", target="17.25")
        connection.execute(
            """INSERT INTO autotrader_ai_calls
               (id, run_id, candidate_id, setup_fingerprint, status, model_id, prompt_version,
                response_hash, latency_ms, cost_usd, decision, rationale, created_at)
               VALUES ('call-1', 'run-orb', 'run-orb:493581450', ?, 'succeeded', 'm', 'p', 'h', 1,
                       '0.05', 'PROPOSE', 'fine', ?)""",
            ("b" * 64, "2026-10-12T14:15:30+00:00"),
        )
    store = _capture_store(tmp_path)
    asyncio.run(track_outcomes(store=store, database=path))

    live = read_live_evidence(path)

    current = code_identity("orb").fingerprint()
    assert live.readable and set(live.sessions) == {current, identity_from_run("ORB_V1", "orb", _orb_prints(risk="0" * 64)).fingerprint()}
    assert live.sessions[current] == frozenset({SESSION})
    trades = sorted(live.trades[current], key=lambda trade: trade.exit_at)
    assert [(trade.net_r, trade.selected, trade.hour_bucket) for trade in trades] == [
        (Decimal("-1.2223"), False, 9),
        (Decimal("1.9375"), True, 10),
    ]
    reports = {report.registry_key: report for report in evaluate_registry(
        replays={"orb": replay_strategy("orb", store)}, live=live, keys=("orb", "gap-v2"),
        replay_notes={"gap-v2": "needs extended bars"},
    )}
    orb_report = reports["orb"]
    assert orb_report.status == NOT_ELIGIBLE
    assert orb_report.live.trades == 2 and orb_report.live_sessions == 1
    assert orb_report.replay is not None and orb_report.replay.trades == 2
    assert {"live_sessions_below_minimum", "replay_trades_below_minimum"} <= set(orb_report.reasons)
    assert reports["gap-v2"].replay is None and "replay_unavailable" in reports["gap-v2"].reasons


def test_reading_evidence_never_writes_and_tolerates_a_database_without_the_tracker(tmp_path):
    from tests.schema_support import migrate_database

    path = migrate_database(tmp_path / "no-draft.db")
    path.chmod(0o400)
    try:
        live = read_live_evidence(path)
    finally:
        path.chmod(0o600)
    assert live.readable and dict(live.trades) == {}
    assert read_live_evidence(tmp_path / "missing.db").readable is False
    assert not (tmp_path / "missing.db").exists()


def test_the_promotion_module_can_only_report():
    source = Path(paper_promotion.__file__).read_text(encoding="utf-8")
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
        "trading.sendable_identities",
        "trading.storage.repository",
        "trading.operations",
        "db.core",
        "aiosqlite",
    )
    assert not [name for name in imported if name.startswith(forbidden)], imported
    for statement in ("INSERT", "UPDATE ", "DELETE", "CREATE ", "DROP ", "mode=rw"):
        assert statement not in source, statement
    assert "?mode=ro" in source
