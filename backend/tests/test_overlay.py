"""The AI overlay scorecard: managed MOMO (AP_MOMO_HOD_V1) against MOMO_HOD_V2 on the same setups.

Three synthetic sessions on a database the code's own migrations built
(v16): one where the AI's picks help, one where managed MOMO fell back to
neutral all day, one where its picks harm. Every expected number below is
worked by hand from the fixture (net R = gross - 0.05R costs):

* help day (day step 1): 20 contracts; the plan focuses 16; the reviewer
  picks 7 (+1.5R each; setup steps 1, 1, 1, 0.5, 0.5, 0.5, 0.5), abstains on
  9 (-0.8R); 4 names stay outside the focus (-0.5R; one never fills); one
  setup only managed MOMO formed (a bar the baseline never saw);
* neutral day: no valid plan, so managed MOMO ran as MOMO_HOD_V2 on every
  name (marked manager_fallback_neutral); outcomes alternate +0.5R / -0.5R;
* harm day (day step 0.75): picks 7 (-1.0R; setup steps 1, 1, 1, 1, 0.5, 0.5,
  0.5), abstains 9 (+1.2R), 4 outside the focus (+0.6R; one not tracked yet);
  one managed setup carries a different entry price (a mismatch).

The scorecard must reproduce them, never write, and carry no identifier.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

import pytest

from tests.schema_support import database_at, migrate_database
from tests.test_nightly_evidence import night  # noqa: F401 - the weekly fixture
from trading.contracts import FixedUniverse, RuleCondition, StrategySpec
from trading.research import overlay
from trading.research.overlay import (
    AI_PICKS_COST_R,
    BASELINE_STRATEGY,
    DEFAULT_DAYS,
    MANAGED_SESSIONS_BELOW_MINIMUM,
    MANAGED_STRATEGY,
    PHASE3_TOLERANCE_R,
    PICKS_BELOW_TOLERANCE,
    OverlayScorecard,
    read_scorecard,
    readiness_lines,
    scorecard_lines,
    scorecard_text,
    sessions_back,
)
from trading.research.replay import FILL_MODEL_ID

HELP, NEUTRAL, HARM = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)
CONTRACTS = tuple(range(1001, 1021))  # twenty synthetic contracts, symbols S01..S20
FOCUS = CONTRACTS[:16]
PICKS = CONTRACTS[:7]
ABSTAINS = CONTRACTS[7:16]
NON_FOCUS = CONTRACTS[16:]
COST = "0.05"
TS = "2026-10-13T12:00:00+00:00"
SIMULATED_AT = "2026-10-17T22:00:00+00:00"
AP_PRINTS = {
    "rules": "momo-rules-v2",
    "risk": "risk-v1",
    "manager_version": "manager-v1",
    "manager_prompt_id": "day-plan-v1",
}
MOMO_PRINTS = {"rules": "momo-rules-v2", "risk": "risk-v1"}


def _spec(name: str) -> StrategySpec:
    return StrategySpec(
        name=name,
        universe=FixedUniverse(symbols=("S01",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale="Scorecard fixture.",
    )


MOMO, AP = _spec(BASELINE_STRATEGY), _spec(MANAGED_STRATEGY)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _symbol(con_id: int) -> str:
    return f"S{con_id - 1000:02d}"


# --- the rows -----------------------------------------------------------------------------


def _strategy(db: sqlite3.Connection, strategy_id: str, spec: StrategySpec) -> None:
    db.execute("INSERT INTO autotrader_strategy_identities VALUES (?, 'owner', ?, NULL)", (strategy_id, TS))
    db.execute(
        """INSERT INTO autotrader_strategy_versions
           (strategy_id, version, content_hash, spec_json, created_at, created_by)
           VALUES (?, 1, ?, ?, ?, 'owner')""",
        (strategy_id, spec.fingerprint(), json.dumps(spec.model_dump(mode="json")), TS),
    )
    db.execute("INSERT INTO autotrader_strategy_lifecycle_v2 VALUES (?, 1, 'shadow', 0, ?)", (strategy_id, TS))


def _run(db: sqlite3.Connection, run_id: str, strategy_id: str, day: date, prints: dict[str, str]) -> None:
    db.execute(
        """INSERT INTO autotrader_runs
           (id, owner_id, source_sha, strategy_id, strategy_version, session_date, mode, status,
            policy_fingerprints_json)
           VALUES (?, 'owner', 'abcdef1', ?, 1, ?, 'shadow', 'completed', ?)""",
        (run_id, strategy_id, day.isoformat(), json.dumps(prints)),
    )
    db.execute(
        """INSERT INTO autotrader_scanner_runs
           (id, run_id, parameters_json, parameters_fingerprint, status, started_at)
           VALUES (?, ?, '{}', ?, 'completed', ?)""",
        (f"{run_id}-scan", run_id, "c" * 64, TS),
    )
    for rank, con_id in enumerate(CONTRACTS, start=1):
        db.execute(
            """INSERT INTO autotrader_candidates
               (id, scanner_run_id, con_id, symbol, rank, contract_json, admission_status)
               VALUES (?, ?, ?, ?, ?, '{}', 'admitted')""",
            (f"{run_id}:{con_id}", f"{run_id}-scan", con_id, _symbol(con_id), rank),
        )


def _setup(
    db: sqlite3.Connection,
    *,
    run_id: str,
    strategy_id: str,
    spec: StrategySpec,
    fingerprint: str,
    con_id: int,
    day: date,
    offset_seconds: int,
    entry: str = "20.00",
    shift_minutes: int = 0,
) -> datetime:
    """One formed setup on the contract's 5-minute bar; returns detected_at."""
    bar = datetime(day.year, day.month, day.day, 14, 30, tzinfo=timezone.utc) + timedelta(
        minutes=5 * (con_id - 1000) + shift_minutes
    )
    detected = bar + timedelta(seconds=offset_seconds)
    db.execute(
        """INSERT INTO autotrader_manager_setups
           (run_id, setup_fingerprint, candidate_id, strategy_id, strategy_version, strategy_name,
            strategy_kind, spec_fingerprint, symbol, con_id, session_date, bar_ts, detected_at,
            hour_bucket, entry_price, stop_price, target_price, atr, levels_json, setup_json,
            created_at)
           VALUES (?, ?, ?, ?, 1, ?, 'momo_hod', ?, ?, ?, ?, ?, ?, 10, ?, '19.50', '21.00', '0.25',
                   '{}', '{}', ?)""",
        (
            run_id, fingerprint, f"{run_id}:{con_id}", strategy_id, spec.name, spec.fingerprint(),
            _symbol(con_id), con_id, day.isoformat(), bar.isoformat(), detected.isoformat(), entry,
            (detected + timedelta(seconds=40)).isoformat(),
        ),
    )
    return detected


def _outcome(db: sqlite3.Connection, *, run_id: str, fingerprint: str, gross: str | None, detected: datetime) -> None:
    if gross is None:
        values = (run_id, fingerprint, FILL_MODEL_ID, SIMULATED_AT, 0, "not_filled", None, "0", "{}")
    else:
        details = {"exit_at": (detected + timedelta(minutes=30)).isoformat()}
        reason = "target" if Decimal(gross) > 0 else "stop"
        values = (run_id, fingerprint, FILL_MODEL_ID, SIMULATED_AT, 1, reason, gross, COST, json.dumps(details))
    db.execute(
        """INSERT INTO autotrader_manager_signal_outcomes
           (run_id, setup_fingerprint, fill_model_id, simulated_at, filled, exit_reason, r_multiple,
            cost_r, details_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        values,
    )


def _plan(
    db: sqlite3.Connection,
    *,
    plan_id: str,
    day: date,
    slot: str,
    outcome: str,
    latency_ms: int | None,
    cost: str,
    day_step: Decimal,
) -> None:
    created = datetime(day.year, day.month, day.day, int(slot[:2]) + 4, int(slot[3:]), tzinfo=timezone.utc)
    valid = outcome == "valid"
    db.execute(
        """INSERT INTO autotrader_manager_plans
           (plan_id, session_date, slot, created_at, valid_until, manager_version, prompt_id, model,
            outcome, regime, strategies_json, focus_json, candidates_json, gates_json, dropped_json,
            context_digest, input_tokens, output_tokens, cost_usd, latency_ms)
           VALUES (?, ?, ?, ?, ?, 'manager-v1', 'day-plan-v1', 'claude-opus-5-5', ?, ?, ?, ?, ?, '{}',
                   '[]', ?, ?, ?, ?, ?)""",
        (
            plan_id,
            day.isoformat(),
            slot,
            created.isoformat(),
            (created + timedelta(minutes=75)).isoformat() if valid else None,
            outcome,
            "trend_up" if valid else None,
            json.dumps({"momo": {"step": float(day_step), "reason": "trend day"}}) if valid else None,
            json.dumps([{"con_id": c, "strategies": ["momo"], "note": "in play"} for c in FOCUS]) if valid else None,
            json.dumps(list(CONTRACTS)),
            "e" * 64 if valid else None,
            6500 if valid else None,
            4200 if valid else None,
            cost,
            latency_ms,
        ),
    )


def _spend(db: sqlite3.Connection, day: date, *, calls: int, spent: str) -> None:
    db.execute(
        """INSERT INTO autotrader_manager_spend (spend_date, purpose, calls, reserved_usd, spent_usd, updated_at)
           VALUES (?, 'plan', ?, '0', ?, ?)""",
        (day.isoformat(), calls, spent, TS),
    )


def _candidate_decision(
    db: sqlite3.Connection, *, run_id: str, con_id: int, outcome: str, fingerprint: str | None
) -> str:
    decision_id = f"{run_id}:decision:{con_id}"
    db.execute(
        """INSERT INTO autotrader_candidate_decisions
           (id, run_id, candidate_id, strategy_id, strategy_version, decision_key, outcome,
            setup_fingerprint, payload_json, created_at)
           VALUES (?, ?, ?, 'strategy-ap', 1, ?, ?, ?, '{}', ?)""",
        (decision_id, run_id, f"{run_id}:{con_id}", f"key:{decision_id}", outcome, fingerprint, TS),
    )
    return decision_id


def _verdict(
    db: sqlite3.Connection, *, run_id: str, fingerprint: str, plan_id: str | None, step: Decimal, note: str | None
) -> None:
    db.execute(
        """INSERT INTO autotrader_manager_verdicts
           (run_id, setup_fingerprint, plan_id, mode, step, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, fingerprint, plan_id, "managed" if plan_id else "neutral", float(step), note, TS),
    )


def _managed_decision(
    db: sqlite3.Connection,
    *,
    decision_id: str,
    run_id: str,
    con_id: int,
    day: date,
    fingerprint: str | None,
    outcome: str,
    plan_id: str | None,
    day_step: Decimal = Decimal("1"),
    setup_step: Decimal = Decimal("1"),
    policy_qty: int | None = None,
    final_qty: int | None = None,
    reason: str | None = None,
    neutral_reason: str | None = None,
    mark: str | None = None,
) -> None:
    managed = plan_id is not None
    db.execute(
        """INSERT INTO autotrader_manager_decisions
           (decision_id, run_id, candidate_id, con_id, setup_fingerprint, reason, session_date, outcome,
            mode, neutral_reason, mark, plan_id, eligibility_id, eligible_json, focus_json, gates_json,
            day_step, setup_step, policy_qty, final_qty, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?)""",
        (
            decision_id, run_id, f"{run_id}:{con_id}", con_id, fingerprint, reason, day.isoformat(),
            outcome, "managed" if managed else "neutral", neutral_reason, mark, plan_id, "f" * 64,
            json.dumps(list(CONTRACTS)), json.dumps(list(FOCUS)) if managed else None,
            float(day_step), float(setup_step), policy_qty, final_qty, TS,
        ),
    )


# --- the three days -------------------------------------------------------------------------


def _gross(scenario: str, con_id: int) -> str | None:
    if scenario == "neutral":
        return "0.5" if con_id % 2 == 0 else "-0.5"
    if con_id in PICKS:
        return "1.5" if scenario == "help" else "-1.0"
    if con_id in ABSTAINS:
        return "-0.8" if scenario == "help" else "1.2"
    if scenario == "help":
        return None if con_id == 1020 else "-0.5"  # one non-focus setup never fills
    return "0.6"


def _day(db: sqlite3.Connection, day: date, scenario: str) -> None:
    momo_run, ap_run = f"momo-{day:%Y%m%d}", f"ap-{day:%Y%m%d}"
    _run(db, momo_run, "strategy-momo", day, MOMO_PRINTS)
    _run(db, ap_run, "strategy-ap", day, AP_PRINTS)
    managed = scenario != "neutral"
    day_step = Decimal("0.75") if scenario == "harm" else Decimal("1")
    plan_id = f"{day:%Y%m%d}-0920-1"
    if scenario == "help":
        for slot, latency in (("09:20", 8000), ("09:46", 9000), ("10:30", 12000)):
            _plan(db, plan_id=f"{day:%Y%m%d}-{slot.replace(':', '')}-1", day=day, slot=slot, outcome="valid",
                  latency_ms=latency, cost="0.12", day_step=day_step)
        _spend(db, day, calls=3, spent="0.36")
    elif scenario == "harm":
        _plan(db, plan_id=plan_id, day=day, slot="09:20", outcome="valid", latency_ms=7000, cost="0.12", day_step=day_step)
        _plan(db, plan_id=f"{day:%Y%m%d}-0946-1", day=day, slot="09:46", outcome="ai_timeout", latency_ms=None,
              cost="0", day_step=day_step)
        _plan(db, plan_id=f"{day:%Y%m%d}-1030-1", day=day, slot="10:30", outcome="valid", latency_ms=20000,
              cost="0.12", day_step=day_step)
        _spend(db, day, calls=3, spent="0.24")
    else:
        _plan(db, plan_id=plan_id, day=day, slot="09:20", outcome="ai_timeout", latency_ms=None, cost="0",
              day_step=day_step)
        _spend(db, day, calls=1, spent="0")
    for con_id in CONTRACTS:
        gross = _gross(scenario, con_id)
        momo_fp = _fp(f"momo:{day}:{con_id}")
        detected = _setup(db, run_id=momo_run, strategy_id="strategy-momo", spec=MOMO, fingerprint=momo_fp,
                          con_id=con_id, day=day, offset_seconds=315)
        tracked = not (scenario == "harm" and con_id == 1020)  # the tracker has not reached it yet
        if tracked:
            _outcome(db, run_id=momo_run, fingerprint=momo_fp, gross=gross, detected=detected)
        if managed and con_id not in FOCUS:
            decision_id = _candidate_decision(db, run_id=ap_run, con_id=con_id, outcome="data_blocked", fingerprint=None)
            _managed_decision(db, decision_id=decision_id, run_id=ap_run, con_id=con_id, day=day, fingerprint=None,
                              outcome="data_blocked", plan_id=plan_id, day_step=day_step, reason="manager_not_in_focus")
            continue
        ap_fp = _fp(f"ap:{day}:{con_id}")
        entry = "20.01" if scenario == "harm" and con_id == 1008 else "20.00"
        ap_detected = _setup(db, run_id=ap_run, strategy_id="strategy-ap", spec=AP, fingerprint=ap_fp, con_id=con_id,
                             day=day, offset_seconds=465, entry=entry)
        if tracked:
            _outcome(db, run_id=ap_run, fingerprint=ap_fp, gross=gross, detected=ap_detected)
        if not managed:
            outcome = "shadow_accepted" if con_id % 2 == 0 else "ai_abstained"
            decision_id = _candidate_decision(db, run_id=ap_run, con_id=con_id, outcome=outcome, fingerprint=ap_fp)
            _verdict(db, run_id=ap_run, fingerprint=ap_fp, plan_id=None, step=Decimal("1"), note=None)
            _managed_decision(db, decision_id=decision_id, run_id=ap_run, con_id=con_id, day=day, fingerprint=ap_fp,
                              outcome=outcome, plan_id=None, policy_qty=1000,
                              final_qty=1000 if outcome == "shadow_accepted" else None,
                              neutral_reason="plan_missing", mark="manager_fallback_neutral")
            continue
        if con_id in PICKS:
            full = 1003 if scenario == "help" else 1004
            setup_step = Decimal("1") if con_id <= full else Decimal("0.5")
            outcome, policy_qty = "shadow_accepted", 1000
            final_qty: int | None = int(Decimal(policy_qty) * day_step * setup_step)
            note = "in play"
        elif con_id % 2:  # the reviewer said ABSTAIN
            outcome, setup_step, policy_qty, final_qty, note = "ai_abstained", Decimal("1"), None, None, None
        else:  # the reviewer said PROPOSE at step 0: skipped before any order
            outcome, setup_step, policy_qty, final_qty, note = "risk_blocked", Decimal("0"), 1000, None, "pass"
        decision_id = _candidate_decision(db, run_id=ap_run, con_id=con_id, outcome=outcome, fingerprint=ap_fp)
        _verdict(db, run_id=ap_run, fingerprint=ap_fp, plan_id=plan_id, step=setup_step, note=note)
        _managed_decision(db, decision_id=decision_id, run_id=ap_run, con_id=con_id, day=day, fingerprint=ap_fp,
                          outcome=outcome, plan_id=plan_id, day_step=day_step, setup_step=setup_step,
                          policy_qty=policy_qty, final_qty=final_qty)
    if scenario == "help":
        # A setup only managed MOMO formed: the baseline never saw that bar.
        extra = _fp(f"ap-only:{day}")
        detected = _setup(db, run_id=ap_run, strategy_id="strategy-ap", spec=AP, fingerprint=extra, con_id=1001,
                          day=day, offset_seconds=465, shift_minutes=200)
        _outcome(db, run_id=ap_run, fingerprint=extra, gross="0.2", detected=detected)


def build_database(path: Path, days: list[tuple[date, str]]) -> Path:
    """A database migrated by the code (v16) with one synthetic session per entry."""
    migrate_database(path)
    # Closed explicitly: a connection left open would checkpoint its WAL later,
    # which the never-writes test would then blame on the reader.
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        _strategy(db, "strategy-momo", MOMO)
        _strategy(db, "strategy-ap", AP)
        for day, scenario in days:
            _day(db, day, scenario)
        db.commit()
    return path


@pytest.fixture
def three_days(tmp_path) -> Path:
    return build_database(tmp_path / "scorecard.db", [(HELP, "help"), (NEUTRAL, "neutral"), (HARM, "harm")])


def _sessions(first: date, count: int) -> list[date]:
    from trading.scheduler.sessions import market_session, next_market_session

    days = [first if market_session(first) is not None else next_market_session(first).session_date]
    while len(days) < count:
        days.append(next_market_session(days[-1]).session_date)
    return days


# --- the pins -------------------------------------------------------------------------------


def test_the_scorecard_names_and_limits_are_pinned():
    from trading.orchestration.session_runner import AP_MOMO_STRATEGY_NAME, MOMO_STRATEGY_NAME
    from trading.research.paper_promotion import MIN_SESSIONS

    assert (MANAGED_STRATEGY, BASELINE_STRATEGY) == (AP_MOMO_STRATEGY_NAME, MOMO_STRATEGY_NAME)
    assert PHASE3_TOLERANCE_R == Decimal("-0.02")
    assert DEFAULT_DAYS == MIN_SESSIONS == 20
    assert overlay.SCORECARD_VERSION == "overlay-scorecard-v1"
    assert overlay.REQUIRED_TABLES == (
        "autotrader_manager_setups",
        "autotrader_manager_signal_outcomes",
        "autotrader_manager_plans",
        "autotrader_manager_verdicts",
        "autotrader_manager_decisions",
        "autotrader_manager_spend",
    )


def test_the_range_is_counted_in_xnys_sessions():
    assert sessions_back(HARM, 3) == HELP
    assert sessions_back(date(2026, 10, 19), 2) == HARM  # Monday back to Friday
    assert sessions_back(HARM, 1) == HARM
    with pytest.raises(ValueError):
        sessions_back(HARM, 0)


# --- the three days ---------------------------------------------------------------------------


EXPECTED_DAYS = [
    "  2026-10-14: 20 pairs (19 filled) · all +0.04R · picks +1.45R (7) · focus +0.16R (16)"
    " · non-focus -0.55R (3) · TAKE 7/7 · ABSTAIN 0/9 · steps -2.90R · fallback 0.0% · plans 3/3"
    " · p50 9.0 s · $0.36",
    "  2026-10-15: 20 pairs (20 filled) · all -0.05R · neutral all day · fallback 100.0% · plans 0/1"
    " · p50 n/a · $0.00",
    "  2026-10-16: 20 pairs (19 filled) · all +0.24R · picks -1.05R (7) · focus +0.19R (16)"
    " · non-focus +0.55R (3) · TAKE 0/7 · ABSTAIN 9/9 · steps +3.02R · fallback 0.0% · plans 2/3"
    " · p50 7.0 s · $0.24",
]
EXPECTED_READINESS = [
    "Phase-3 readiness (AP_MOMO_HOD_V1 vs MOMO_HOD_V2 on the same setups, net R)",
    "  2 managed sessions of 3 · 60 paired setups · 32 judged · 14 AI-picked filled",
    "  all +0.08R/trade · AI-picked +0.20R · focus +0.17R · non-focus +0.00R",
    "  picks - all +0.03R (90% bounds -1.24R..+1.29R): AI review keeps its say",
    "  not ready: managed sessions 2/20",
]


def test_three_synthetic_days_score_exactly_as_worked_by_hand(three_days):
    card = read_scorecard(three_days, end=HARM, days=3)
    lines = scorecard_lines(card)

    assert lines[:4] == [
        "AI overlay scorecard (overlay-scorecard-v1): AP_MOMO_HOD_V1 vs MOMO_HOD_V2 paired on the same"
        " contract and bar, net R after costs",
        "Range 2026-10-14..2026-10-16: 3 sessions with data, 2 managed by a plan",
        "Paired setups 60: 58 filled, 1 not tracked yet, 1 managed-only, 1 price mismatches",
        "Net R per trade: all +0.08R (58) · AI-picked +0.20R (14) · focus +0.17R (32) · non-focus +0.00R (6)",
    ]
    assert lines[4].startswith("90% day-clustered bounds: all ")
    assert lines[5].startswith("Differences: picks - all +0.03R (-1.24R..+1.29R) · focus - all ")
    assert lines[6:13] == [
        "Managed reviewer: TAKE 7/14 won (50.0%) · ABSTAIN 9/18 won (50.0%) · judged 32",
        "Size steps on picks: +2.92R with steps vs +2.80R at full size (+0.12R)",
        "Managed decisions 60: 13.3% not in focus · 33.3% fallback neutral",
        "Plans 7: 71.4% valid · latency p50 9.0 s, p95 20.0 s · spend $0.60 (budget $5.00/day)",
        "Do no harm: AI review keeps its say (picks - all +0.03R, upper bound +1.29R)",
        "Phase 3 (paper-promotion-v1, picks vs all judged): not ready: managed sessions 2/20",
        "Per session:",
    ]
    assert lines[13:] == EXPECTED_DAYS
    assert scorecard_text(card) == "\n".join(lines)
    assert readiness_lines(card) == EXPECTED_READINESS

    # The bounds are paper_promotion's day-clustered bootstrap: whole days resampled.
    assert (card.bounds["all"].days, card.bounds["all"].trades) == (3, 58)
    assert (card.bounds["picked"].days, card.bounds["picked"].trades) == (2, 14)
    assert card.bounds["all"].lower <= card.bounds["all"].estimate <= card.bounds["all"].upper
    assert card.bounds["picked_minus_all"].payload()["confidence"] == "0.90"
    assert card.phase3 is not None and card.phase3.reasons == (MANAGED_SESSIONS_BELOW_MINIMUM,)
    assert card.phase3.ai.status == "active"
    assert (card.phase3.ai.all_trades, card.phase3.ai.selected_trades) == (32, 14)


def test_the_json_payload_carries_the_same_numbers(three_days):
    payload = read_scorecard(three_days, end=HARM, days=3).payload()

    assert payload["schema"] == "autotrader-v1/manager-scorecard" and payload["version"] == 1
    assert payload["range"] == {"start": "2026-10-14", "end": "2026-10-16", "sessions": 3, "managed_sessions": 2}
    assert payload["pairs"] == {
        "total": 60, "filled": 58, "untracked": 1, "managed_only": 1, "price_mismatches": 1, "judged": 32,
    }
    assert payload["r"]["picked"] == {"trades": 14, "total_r": "2.8000", "mean_r": "0.2000", "wins": 7, "hit_rate": "0.5000"}
    assert payload["size_steps"] == {"scaled_r": "2.9188", "unscaled_r": "2.8000"}
    assert payload["decisions"] == {
        "total": 60, "fallback_neutral": 20, "fallback_share": "0.3333", "not_in_focus": 8, "not_in_focus_share": "0.1333",
    }
    assert payload["plans"] == {
        "attempts": 7, "valid": 5, "validity": "0.7143", "latency_p50_ms": 9000, "latency_p95_ms": 20000,
        "plan_cost_usd": "0.6000", "spent_usd": "0.6000",
    }
    assert payload["phase3"]["status"] == "active" and payload["phase3"]["ready"] is False
    assert payload["phase3"]["reasons"] == [MANAGED_SESSIONS_BELOW_MINIMUM]
    assert payload["phase3"]["grants"] == "nothing: phase 3 needs the owner's review and sign-off"
    assert [day["session_date"] for day in payload["days"]] == ["2026-10-14", "2026-10-15", "2026-10-16"]
    assert payload["days"][1]["decisions"]["fallback_share"] == "1.0000"
    assert payload["days"][2]["plans"]["latency_p95_ms"] == 20000


def test_the_range_bounds_the_sessions(three_days):
    card = read_scorecard(three_days, end=NEUTRAL, days=2)
    assert [day.session_date for day in card.days] == [HELP, NEUTRAL]
    assert card.pairs == 40 and card.managed_sessions == 1

    everything = read_scorecard(three_days, end=HARM, days=None)
    assert everything.start is None and everything.pairs == 60
    assert read_scorecard(three_days, end=date(2026, 10, 13), days=5).pairs == 0


# --- the phase-3 check --------------------------------------------------------------------------


def test_harm_on_every_managed_day_is_advisory_and_below_tolerance(tmp_path):
    path = build_database(tmp_path / "harm.db", [(NEUTRAL, "harm"), (HARM, "harm")])

    card = read_scorecard(path, end=HARM, days=2)

    assert card.phase3 is not None
    assert card.phase3.ai.status == "advisory"
    assert card.phase3.reasons == (MANAGED_SESSIONS_BELOW_MINIMUM, AI_PICKS_COST_R, PICKS_BELOW_TOLERANCE)
    assert card.phase3.ai.difference.estimate == Decimal("-1.2375")
    assert card.phase3.ai.difference.upper < 0
    assert readiness_lines(card)[-2:] == [
        "  picks - all -1.24R (90% bounds -1.24R..-1.24R): AI advisory (its picks cost R)",
        "  not ready: managed sessions 2/20, the AI's picks measurably cost R, picks trail all setups by"
        " more than 0.02R",
    ]


def test_twenty_helpful_managed_sessions_are_ready_for_the_owners_review(tmp_path):
    days = _sessions(HELP, 20)
    path = build_database(tmp_path / "help.db", [(day, "help") for day in days])

    card = read_scorecard(path, end=days[-1], days=20)

    assert card.start == days[0] and card.managed_sessions == 20
    assert card.phase3 is not None and card.phase3.ready and card.phase3.reasons == ()
    assert card.phase3.ai.status == "active"
    assert card.phase3.ai.difference.estimate == Decimal("1.29375")
    assert overlay.phase3_line(card.phase3) == (
        "Phase 3 (paper-promotion-v1, picks vs all judged): READY for the owner's phase-3 review"
    )
    assert readiness_lines(card)[-1] == "  READY for the owner's phase-3 review"


def test_the_check_is_paper_promotions_own(three_days):
    from trading.research.paper_promotion import EvidenceTrade, ai_do_no_harm

    card = read_scorecard(three_days, end=HARM, days=3)
    assert card.identity is not None and card.identity.manager_version == "manager-v1"
    assert card.identity.prompt_id == "day-plan-v1"
    # Recomputed from the pairs: 16 judged trades a managed day, 7 of them picked.
    trades = []
    for day, pick_r, pass_r in ((HELP, "1.45", "-0.85"), (HARM, "-1.05", "1.15")):
        moment = datetime(day.year, day.month, day.day, 15, tzinfo=timezone.utc)
        trades += [EvidenceTrade(day, moment, Decimal(pick_r), selected=True) for _ in range(7)]
        trades += [EvidenceTrade(day, moment, Decimal(pass_r)) for _ in range(9)]
    expected = ai_do_no_harm(trades, identity=card.identity)
    assert card.phase3 is not None
    assert card.phase3.ai.payload() == expected.payload()


# --- read-only, unavailable, no identifiers ------------------------------------------------------


def test_the_scorecard_never_writes(three_days):
    path = three_days
    digest = sha256(path.read_bytes()).hexdigest()
    assert sorted(item.name for item in path.parent.iterdir()) == ["scorecard.db"]  # closed cleanly
    path.chmod(0o444)
    try:
        card = read_scorecard(path, end=HARM, days=3)
    finally:
        path.chmod(0o644)

    assert card.readable and card.pairs == 60
    assert sha256(path.read_bytes()).hexdigest() == digest
    # A read-only handle on a WAL database may leave the empty -shm/-wal sidecars
    # SQLite needs to read; nothing else appears and the -wal carries no frame.
    leftovers = sorted(item.name for item in path.parent.iterdir())
    assert set(leftovers) <= {"scorecard.db", "scorecard.db-shm", "scorecard.db-wal"}
    if (path.parent / "scorecard.db-wal").exists():
        assert (path.parent / "scorecard.db-wal").stat().st_size == 0
    source = Path(overlay.__file__).read_text(encoding="utf-8")
    assert "?mode=ro" in source
    for token in ("INSERT", "UPDATE", "DELETE", "transaction(", "aiosqlite", "mode=rw", "get_db"):
        assert token not in source


def test_a_database_without_the_manager_tables_is_unavailable(tmp_path):
    behind = database_at(tmp_path / "behind.db", 12)
    card = read_scorecard(behind, end=HARM, days=3)
    assert not card.readable and card.note == "schema_migration_pending: no autotrader_manager_setups"
    assert readiness_lines(card) == [
        "Phase-3 readiness (AP_MOMO_HOD_V1 vs MOMO_HOD_V2 on the same setups, net R):"
        " unavailable (schema_migration_pending: no autotrader_manager_setups)"
    ]
    assert scorecard_lines(card)[1] == (
        "Range 2026-10-14..2026-10-16: unavailable (schema_migration_pending: no autotrader_manager_setups)"
    )
    missing = read_scorecard(tmp_path / "absent.db", end=HARM, days=3)
    assert (missing.readable, missing.note) == (False, "no database")
    assert not (tmp_path / "absent.db").exists()


def test_an_empty_scorecard_says_so(tmp_path):
    card = read_scorecard(migrate_database(tmp_path / "empty.db"), end=HARM, days=3)
    assert card.readable and card.pairs == 0 and card.phase3 is not None
    assert readiness_lines(card) == [
        "Phase-3 readiness (AP_MOMO_HOD_V1 vs MOMO_HOD_V2 on the same setups, net R): no paired setups yet",
        "  not ready: managed sessions 0/20, judged setups 0/30, AI-picked setups 0/10,"
        " no bounds yet (two managed days with trades needed)",
    ]
    assert scorecard_lines(card)[-2:] == ["Per session:", "  none"]
    unreadable = OverlayScorecard(start=None, end=HARM, readable=False, note="failed_valueerror")
    assert readiness_lines(unreadable)[0].endswith(": unavailable (failed_valueerror)")


# Anything the fixture uses as an identifier: fingerprints, plan/run/decision ids, symbols, con_ids.
_IDENTIFIER = re.compile(
    r"[0-9a-f]{64}|\d{8}-\d{4}-\d|momo-2026|ap-2026|:decision:|\bS0[1-9]\b|\bS1[0-9]\b|\bS20\b|\b10[01][0-9]\b|\b1020\b"
)


def _no_identifiers(text: str) -> None:
    assert _IDENTIFIER.search(text) is None, text


# --- autotraderctl manager scorecard ------------------------------------------------------------


def test_the_cli_prints_the_scorecard_and_exits_zero(three_days, capsys):
    from trading import cli

    assert cli.main(["manager", "scorecard", "--db-path", str(three_days), "--days", "3", "--date", "2026-10-16"]) == 0
    out = capsys.readouterr().out
    assert out == scorecard_text(read_scorecard(three_days, end=HARM, days=3)) + "\n"
    _no_identifiers(out)

    assert cli.main(["manager", "scorecard", "--db-path", str(three_days), "--date", "2026-10-16"]) == 0
    assert "Range 2026-09-21..2026-10-16: 3 sessions with data" in capsys.readouterr().out  # 20 sessions

    assert cli.main(["manager", "scorecard", "--db-path", str(three_days)]) == 0  # the default: today, ET
    assert capsys.readouterr().out.startswith("AI overlay scorecard (overlay-scorecard-v1)")


def test_the_cli_json_is_the_payload(three_days, capsys):
    from trading import cli

    assert cli.main(["manager", "scorecard", "--db-path", str(three_days), "--days", "3", "--date", "2026-10-16", "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload == read_scorecard(three_days, end=HARM, days=3).payload()
    assert set(payload) == {
        "schema", "version", "scorecard", "managed_strategy", "baseline_strategy", "fill_model_id", "readable",
        "note", "range", "pairs", "r", "bounds", "hit_rates", "size_steps", "decisions", "plans", "phase3", "days",
    }
    _no_identifiers(out)


def test_the_cli_uses_the_configured_database_and_refuses_what_it_cannot_score(tmp_path, monkeypatch, capsys):
    from config import cfg
    from trading import cli

    build_database(tmp_path / "configured.db", [(HELP, "help")])
    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "configured.db"))
    assert cli.main(["manager", "scorecard", "--days", "1", "--date", "2026-10-14"]) == 0
    assert "1 sessions with data, 1 managed by a plan" in capsys.readouterr().out

    behind = database_at(tmp_path / "behind.db", 12)
    assert cli.main(["manager", "scorecard", "--db-path", str(behind)]) == 1
    err = capsys.readouterr().err
    assert "scorecard unavailable: schema_migration_pending" in err and "autotraderctl db migrate" in err

    assert cli.main(["manager", "scorecard", "--db-path", str(tmp_path / "absent.db")]) == 1
    assert "no database" in capsys.readouterr().err
    assert not (tmp_path / "absent.db").exists()

    with pytest.raises(SystemExit) as refused:
        cli.main(["manager", "scorecard", "--days", "0"])
    assert refused.value.code == 2


# --- the weekly summary -------------------------------------------------------------------------


def test_the_weekly_summary_gains_the_phase3_section_and_nothing_else(three_days):
    from trading.research.live_ledger import LiveLedger
    from trading.research.nightly import NightlyEvidence
    from trading.research.outcomes import TrackerReport
    from trading.research.report_notify import compose_summary, evidence_text, phase3_lines

    card = read_scorecard(three_days, end=HARM, days=3)
    tracker = TrackerReport(status="skipped", fill_model_id=FILL_MODEL_ID, reason="no_database")
    before = NightlyEvidence(tracker=tracker, promotion=(), live_r=LiveLedger())
    after = replace(before, phase3=card)

    assert phase3_lines(before) == [] and phase3_lines(after) == EXPECTED_READINESS
    assert evidence_text(after) == evidence_text(before) + "\n\n" + "\n".join(EXPECTED_READINESS)
    assert compose_summary("Research replay", after) == evidence_text(after) + "\n\nResearch replay"
    assert compose_summary("Research replay", before) == evidence_text(before) + "\n\nResearch replay"


def test_the_nights_weekly_summary_carries_the_section_before_the_replay(night):
    """The existing weekly fixture (tests/test_nightly_evidence.py): its lines are
    untouched, the phase-3 block follows live R, and evidence.json/.txt carry it."""
    from tests.test_nightly_evidence import TUESDAY, _environment, _et
    from trading.research.replay import run_replay_cli

    assert run_replay_cli([], notify="weekly", environment=_environment(night, _et(TUESDAY, 17, 40))) == 0

    (summary,) = night["sent"]
    sections = [block.splitlines() for block in summary.split("\n\n")]
    assert sections[0][0] == "Paper promotion (paper-promotion-v1; reports only, nothing is granted)"
    assert sections[1][0].startswith("Window research") and sections[2][0].startswith("Live R")
    assert sections[3] == [
        "Phase-3 readiness (AP_MOMO_HOD_V1 vs MOMO_HOD_V2 on the same setups, net R): no paired setups yet",
        "  not ready: managed sessions 0/20, judged setups 0/30, AI-picked setups 0/10,"
        " no bounds yet (two managed days with trades needed)",
    ]
    assert sections[4][0] == "Research replay 2026-10-12..2026-10-12: 1 sessions, 2 symbols"
    directory = night["root"] / "reports" / "2026-10-12"
    evidence_report = json.loads((directory / "evidence.json").read_text())
    assert evidence_report["phase3"]["readable"] is True
    assert evidence_report["phase3"]["pairs"]["total"] == 0
    assert evidence_report["phase3"]["range"]["start"] is None  # every session so far
    assert (directory / "evidence.txt").read_text().rstrip("\n").endswith(sections[3][-1])
