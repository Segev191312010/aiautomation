"""The paper promotion rule (owner decision "earn it by results", 2026-10-13).

This module only reports. For each strategy it says ELIGIBLE or NOT_ELIGIBLE
for the owner's PAPER promotion review, and why; it never grants, records or
changes any authority. Eligibility is one prerequisite among the spec's
steps: an independent safety review, the owner's review of the shadow
decisions, an owner decision, then 5 probation sessions at half size.
``trading/evidence/validator.py`` is a different thing: the pinned gate for
real money, which this module neither reads nor feeds.

The rule is fixed before any data is seen (``RULE_VERSION`` and the constants
below are pinned by a test):

* one evidence identity: the rules fingerprint, the entry window and the risk
  policy (plus the manager version and prompt id when managed). Any change is
  a new identity, so its evidence starts again from zero. An identity's size
  multiplier (``trading.risk.identity_sizing``) is deliberately not part of it:
  every statistic here is in R, which the quantity cancels out of, so a size
  step never restarts the evidence (it is an event in ``size_steps``,
  ``trading.research.size_history``; a test pins the identity across tables);
* at least ``MIN_SESSIONS`` live sessions under that identity and as many
  replayed sessions;
* at least ``MIN_REPLAY_TRADES`` replay trades and ``MIN_LIVE_SETUPS`` filled
  live-simulated setups (the nightly tracker's outcomes);
* on both samples, the day-clustered bootstrap lower bound (one-sided,
  ``BOOTSTRAP_CONFIDENCE``) of the expectancy after costs is above zero, and
  the profit factor after costs is at least ``MIN_PROFIT_FACTOR``;
* the replay's maximum drawdown (net R, in order of exit) is at most
  ``MAX_REPLAY_DRAWDOWN_R``.

Bootstrap: the sessions of a sample are resampled with replacement (a day
keeps all of its trades together, because trades on one day share the
market), ``BOOTSTRAP_RESAMPLES`` times; each resample's statistic is total
net R over its trade count. The bounds are nearest-rank percentiles of those
statistics. The random generator is seeded from the identity and the sample,
so a report is reproducible.

AI do-no-harm: on the live setups the AI judged, its selection (today the
reviewer's PROPOSE; later the manager's selection) against all of them,
paired by day. If the bootstrap upper bound of (selected - all) is below
zero the AI is reported ``advisory`` for that strategy: following it
measurably costs R.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import sqlite3
from typing import Any, Literal
from urllib.parse import quote

from trading.contracts import RiskPolicy, fingerprint_payload
from trading.policies import OpeningRangeBreakoutPolicy
from trading.research.catalogue import EVALUATOR_FACTORIES, REGISTRY_KEYS, catalogue_name
from trading.research.costs import DEFAULT_COST_MODEL, CostModel
from trading.research.replay import FILL_MODEL_ID, ReplayResult
from trading.research.stats import max_drawdown_r
from trading.rules.gap_go import GapGoConfig
from trading.rules.momo import MomoConfig
from trading.rules.vwap_pb import VwapPbConfig
from trading.storage.migrations import SCHEMA_READ_TIMEOUT_SECONDS

RULE_VERSION = "paper-promotion-v1"
MIN_SESSIONS = 20
MIN_REPLAY_TRADES = 100
MIN_LIVE_SETUPS = 30
BOOTSTRAP_CONFIDENCE = Decimal("0.90")  # one-sided lower (upper) bound
BOOTSTRAP_RESAMPLES = 10_000
MIN_PROFIT_FACTOR = Decimal("1.2")
MAX_REPLAY_DRAWDOWN_R = Decimal("15")
# The AI verdict needs a selection large enough that one bad day cannot decide it.
MIN_MANAGED_SETUPS = 10

ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
AI_ADVISORY = "advisory"
AI_ACTIVE = "active"
AI_INSUFFICIENT = "insufficient_data"
# A V1 config embeds its own window; a windowed strategy records it apart.
WINDOW_IN_RULES = "embedded_in_rules"
# The run fingerprint that carries each kind's V1 rules.
V1_RULES_KEYS: dict[str, str] = {
    "orb": "opening_range_breakout",
    "momo_hod": "momo_hod",
    "vwap_pb": "vwap_pb",
    "gap_go": "gap_go",
}
# Phase 2 (managed identities) records these in the run's policy fingerprints.
MANAGER_VERSION_KEY = "manager_version"
MANAGER_PROMPT_KEY = "manager_prompt_id"

# Named reasons a strategy is not eligible, in check order.
LIVE_SESSIONS_BELOW_MINIMUM = "live_sessions_below_minimum"
REPLAY_UNAVAILABLE = "replay_unavailable"
REPLAY_SESSIONS_BELOW_MINIMUM = "replay_sessions_below_minimum"
REPLAY_TRADES_BELOW_MINIMUM = "replay_trades_below_minimum"
LIVE_SETUPS_BELOW_MINIMUM = "live_setups_below_minimum"
REPLAY_LOWER_BOUND_NOT_POSITIVE = "replay_expectancy_lower_bound_not_positive"
LIVE_LOWER_BOUND_NOT_POSITIVE = "live_expectancy_lower_bound_not_positive"
REPLAY_PROFIT_FACTOR_BELOW_MINIMUM = "replay_profit_factor_below_minimum"
LIVE_PROFIT_FACTOR_BELOW_MINIMUM = "live_profit_factor_below_minimum"
REPLAY_DRAWDOWN_ABOVE_MAXIMUM = "replay_drawdown_above_maximum"
LIVE_EVIDENCE_UNREADABLE = "live_evidence_unreadable"

_R_QUANTUM = Decimal("0.0001")
_UNITS = Decimal("10000")
_ZERO = Decimal("0")

Sample = Literal["replay", "live"]


# --- evidence identity ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    """What the evidence is about; any change starts the evidence window again."""

    strategy: str
    rules: str
    entry_window: str
    risk_policy: str
    manager_version: str | None = None
    prompt_id: str | None = None

    def fingerprint(self) -> str:
        return fingerprint_payload(
            {
                "rule": RULE_VERSION,
                "strategy": self.strategy,
                "rules": self.rules,
                "entry_window": self.entry_window,
                "risk_policy": self.risk_policy,
                "manager_version": self.manager_version,
                "prompt_id": self.prompt_id,
            }
        )

    def short(self) -> str:
        return self.fingerprint()[:12]


def identity_from_run(
    strategy_name: str, strategy_kind: str | None, prints: Mapping[str, object]
) -> EvidenceIdentity | None:
    """The identity a run recorded (``autotrader_runs.policy_fingerprints_json``)."""
    rules = prints.get("rules")
    if rules is None and strategy_kind is not None and strategy_kind in V1_RULES_KEYS:
        rules = prints.get(V1_RULES_KEYS[strategy_kind])
    risk = prints.get("risk")
    if not isinstance(rules, str) or not isinstance(risk, str):
        return None
    window = prints.get("entry_window", WINDOW_IN_RULES)
    manager = prints.get(MANAGER_VERSION_KEY)
    prompt = prints.get(MANAGER_PROMPT_KEY)
    return EvidenceIdentity(
        strategy=strategy_name,
        rules=rules,
        entry_window=str(window),
        risk_policy=risk,
        manager_version=None if manager is None else str(manager),
        prompt_id=None if prompt is None else str(prompt),
    )


def code_identity(registry_key: str, *, risk_policy: RiskPolicy | None = None) -> EvidenceIdentity:
    """The identity this code runs a registered strategy under (live and replay alike)."""
    evaluator = EVALUATOR_FACTORIES[registry_key]()
    evidence = getattr(evaluator, "policy_fingerprints", None)
    risk = (risk_policy or RiskPolicy()).fingerprint()
    name = catalogue_name(registry_key)
    if callable(evidence):
        prints = evidence()
        return EvidenceIdentity(
            strategy=name, rules=prints["rules"], entry_window=prints["entry_window"], risk_policy=risk
        )
    kind = evaluator.strategy_kind
    rules = {
        "orb": lambda: OpeningRangeBreakoutPolicy().fingerprint(),
        "momo_hod": lambda: MomoConfig().fingerprint(),
        "vwap_pb": lambda: VwapPbConfig().fingerprint(),
        "gap_go": lambda: GapGoConfig().fingerprint(),
    }[kind]()
    return EvidenceIdentity(strategy=name, rules=rules, entry_window=WINDOW_IN_RULES, risk_policy=risk)


# --- samples and the bootstrap ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceTrade:
    """One trade's R after costs, with the day it belongs to."""

    session_date: date
    exit_at: datetime
    net_r: Decimal
    hour_bucket: int | None = None
    # The AI judged the setup (a succeeded review) and, if so, picked it.
    reviewed: bool = True
    selected: bool = False


@dataclass(frozen=True, slots=True)
class BootstrapBound:
    estimate: Decimal | None
    lower: Decimal | None
    upper: Decimal | None
    days: int
    trades: int
    resamples: int

    def payload(self) -> dict[str, object]:
        return {
            "estimate": _text(self.estimate),
            "lower": _text(self.lower),
            "upper": _text(self.upper),
            "days": self.days,
            "trades": self.trades,
            "resamples": self.resamples,
            "confidence": str(BOOTSTRAP_CONFIDENCE),
        }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(_R_QUANTUM, rounding=ROUND_HALF_EVEN))


def _units(value: Decimal) -> int:
    return int((value.quantize(_R_QUANTUM, rounding=ROUND_HALF_EVEN) * _UNITS).to_integral_value())


def _seed(label: str) -> int:
    return int(sha256(label.encode("utf-8")).hexdigest()[:16], 16)


def _ranks(resamples: int, confidence: Decimal) -> tuple[int, int]:
    """Nearest-rank indexes of the one-sided lower and upper bounds."""
    alpha = Decimal("1") - confidence
    lower = math.ceil(alpha * resamples) - 1
    upper = math.ceil(confidence * resamples) - 1
    return max(lower, 0), min(upper, resamples - 1)


def _days(trades: Iterable[EvidenceTrade]) -> list[tuple[date, int, int, int, int]]:
    """(day, total units, trades, selected units, selected trades), by day."""
    grouped: dict[date, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for trade in trades:
        units = _units(trade.net_r)
        row = grouped[trade.session_date]
        row[0] += units
        row[1] += 1
        if trade.selected:
            row[2] += units
            row[3] += 1
    return [(day, *grouped[day]) for day in sorted(grouped)]  # type: ignore[misc]


def day_clustered_bootstrap(
    trades: Sequence[EvidenceTrade],
    *,
    seed_label: str,
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence: Decimal = BOOTSTRAP_CONFIDENCE,
) -> BootstrapBound:
    """Bounds of the expectancy (mean net R per trade), resampling whole days."""
    days = _days(trades)
    count = sum(day[2] for day in days)
    estimate = None if not count else Decimal(sum(day[1] for day in days)) / count / _UNITS
    if len(days) < 2:
        return BootstrapBound(estimate, None, None, len(days), count, 0)
    rng = random.Random(_seed(seed_label))
    size = len(days)
    statistics: list[Decimal] = []
    for _ in range(resamples):
        total = number = 0
        for _pick in range(size):
            day = days[rng.randrange(size)]
            total += day[1]
            number += day[2]
        statistics.append(Decimal(total) / number / _UNITS)
    statistics.sort()
    low, high = _ranks(resamples, confidence)
    return BootstrapBound(estimate, statistics[low], statistics[high], size, count, resamples)


def paired_difference_bootstrap(
    trades: Sequence[EvidenceTrade],
    *,
    seed_label: str,
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence: Decimal = BOOTSTRAP_CONFIDENCE,
) -> BootstrapBound:
    """Bounds of E(selected) - E(all) on the same resampled days."""
    days = _days(trades)
    total = sum(day[1] for day in days)
    count = sum(day[2] for day in days)
    selected_total = sum(day[3] for day in days)
    selected_count = sum(day[4] for day in days)
    if not count or not selected_count:
        return BootstrapBound(None, None, None, len(days), selected_count, 0)
    estimate = (Decimal(selected_total) / selected_count - Decimal(total) / count) / _UNITS
    if len(days) < 2:
        return BootstrapBound(estimate, None, None, len(days), selected_count, 0)
    rng = random.Random(_seed(seed_label))
    size = len(days)
    statistics: list[Decimal] = []
    for _ in range(resamples):
        all_total = all_count = picked_total = picked_count = 0
        for _pick in range(size):
            day = days[rng.randrange(size)]
            all_total += day[1]
            all_count += day[2]
            picked_total += day[3]
            picked_count += day[4]
        if not picked_count:
            continue  # this resample drew no selected trade: no difference to measure
        statistics.append(
            (Decimal(picked_total) / picked_count - Decimal(all_total) / all_count) / _UNITS
        )
    if not statistics:
        return BootstrapBound(estimate, None, None, size, selected_count, 0)
    statistics.sort()
    low, high = _ranks(len(statistics), confidence)
    return BootstrapBound(estimate, statistics[low], statistics[high], size, selected_count, len(statistics))


def profit_factor(values: Iterable[Decimal]) -> Decimal | None:
    wins = losses = _ZERO
    for value in values:
        if value > 0:
            wins += value
        elif value < 0:
            losses -= value
    return None if losses == 0 else wins / losses


@dataclass(frozen=True, slots=True)
class SampleEvidence:
    sample: Sample
    sessions: int
    trades: int
    expectancy_r: Decimal | None
    profit_factor: Decimal | None
    max_drawdown_r: Decimal
    bound: BootstrapBound

    def payload(self) -> dict[str, object]:
        return {
            "sample": self.sample,
            "sessions": self.sessions,
            "trades": self.trades,
            "expectancy_r": _text(self.expectancy_r),
            "profit_factor": _text(self.profit_factor),
            "max_drawdown_r": _text(self.max_drawdown_r),
            "bootstrap": self.bound.payload(),
        }


def sample_evidence(
    sample: Sample,
    trades: Sequence[EvidenceTrade],
    *,
    sessions: int,
    identity: EvidenceIdentity,
) -> SampleEvidence:
    ordered = sorted(trades, key=lambda trade: (trade.exit_at, trade.session_date))
    values = [trade.net_r for trade in ordered]
    return SampleEvidence(
        sample=sample,
        sessions=sessions,
        trades=len(values),
        expectancy_r=(sum(values, _ZERO) / len(values)) if values else None,
        profit_factor=profit_factor(values),
        max_drawdown_r=max_drawdown_r(values),
        bound=day_clustered_bootstrap(ordered, seed_label=f"{identity.fingerprint()}:{sample}"),
    )


# --- the rule ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AIDoNoHarm:
    status: str
    selected_trades: int
    all_trades: int
    difference: BootstrapBound

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "selection": "ai_review_propose",
            "selected_trades": self.selected_trades,
            "all_trades": self.all_trades,
            "difference": self.difference.payload(),
        }


def ai_do_no_harm(live: Sequence[EvidenceTrade], *, identity: EvidenceIdentity) -> AIDoNoHarm:
    """``advisory`` when following the AI's selection measurably costs R.

    Only setups the AI actually judged take part: one it never saw (its
    per-run budget spent, a timeout) is neither picked nor passed over, and
    counting it would compare the morning's picks with the afternoon.
    """
    judged = [trade for trade in live if trade.reviewed]
    selected = sum(1 for trade in judged if trade.selected)
    difference = paired_difference_bootstrap(judged, seed_label=f"{identity.fingerprint()}:ai")
    if len(judged) < MIN_LIVE_SETUPS or selected < MIN_MANAGED_SETUPS or difference.upper is None:
        status = AI_INSUFFICIENT
    elif difference.upper < 0:
        status = AI_ADVISORY
    else:
        status = AI_ACTIVE
    return AIDoNoHarm(status, selected, len(judged), difference)


@dataclass(frozen=True, slots=True)
class PromotionReport:
    """ELIGIBLE or not, and why. A report only: it grants nothing."""

    strategy: str
    registry_key: str | None
    identity: EvidenceIdentity
    reasons: tuple[str, ...]
    live_sessions: int
    live: SampleEvidence
    replay: SampleEvidence | None
    ai: AIDoNoHarm
    replay_note: str = ""

    @property
    def eligible(self) -> bool:
        return not self.reasons

    @property
    def status(self) -> str:
        return ELIGIBLE if self.eligible else NOT_ELIGIBLE

    def payload(self) -> dict[str, object]:
        return {
            "rule": RULE_VERSION,
            "strategy": self.strategy,
            "registry_key": self.registry_key,
            "status": self.status,
            "grants": "nothing: eligibility is evidence for the owner's review only",
            "reasons": list(self.reasons),
            "identity": {
                "fingerprint": self.identity.fingerprint(),
                "rules": self.identity.rules,
                "entry_window": self.identity.entry_window,
                "risk_policy": self.identity.risk_policy,
                "manager_version": self.identity.manager_version,
                "prompt_id": self.identity.prompt_id,
            },
            "live_sessions": self.live_sessions,
            "live": self.live.payload(),
            "replay": None if self.replay is None else self.replay.payload(),
            "replay_note": self.replay_note,
            "ai_do_no_harm": self.ai.payload(),
            "thresholds": thresholds(),
        }


def thresholds() -> dict[str, str]:
    return {
        "min_sessions": str(MIN_SESSIONS),
        "min_replay_trades": str(MIN_REPLAY_TRADES),
        "min_live_setups": str(MIN_LIVE_SETUPS),
        "bootstrap_confidence_one_sided": str(BOOTSTRAP_CONFIDENCE),
        "bootstrap_resamples": str(BOOTSTRAP_RESAMPLES),
        "min_profit_factor": str(MIN_PROFIT_FACTOR),
        "max_replay_drawdown_r": str(MAX_REPLAY_DRAWDOWN_R),
        "min_managed_setups": str(MIN_MANAGED_SETUPS),
    }


def evaluate_promotion(
    *,
    identity: EvidenceIdentity,
    registry_key: str | None,
    live: Sequence[EvidenceTrade],
    live_sessions: int,
    replay: Sequence[EvidenceTrade] | None,
    replay_sessions: int = 0,
    replay_note: str = "",
) -> PromotionReport:
    """Apply the rule to one identity's evidence (pure; nothing is read or written)."""
    live_evidence = sample_evidence("live", live, sessions=live_sessions, identity=identity)
    replay_evidence = (
        None
        if replay is None
        else sample_evidence("replay", replay, sessions=replay_sessions, identity=identity)
    )
    reasons: list[str] = []
    if live_sessions < MIN_SESSIONS:
        reasons.append(LIVE_SESSIONS_BELOW_MINIMUM)
    if replay_evidence is None:
        reasons.append(REPLAY_UNAVAILABLE)
    else:
        if replay_evidence.sessions < MIN_SESSIONS:
            reasons.append(REPLAY_SESSIONS_BELOW_MINIMUM)
        if replay_evidence.trades < MIN_REPLAY_TRADES:
            reasons.append(REPLAY_TRADES_BELOW_MINIMUM)
    if live_evidence.trades < MIN_LIVE_SETUPS:
        reasons.append(LIVE_SETUPS_BELOW_MINIMUM)
    if replay_evidence is not None:
        lower = replay_evidence.bound.lower
        if lower is None or lower <= 0:
            reasons.append(REPLAY_LOWER_BOUND_NOT_POSITIVE)
    lower = live_evidence.bound.lower
    if lower is None or lower <= 0:
        reasons.append(LIVE_LOWER_BOUND_NOT_POSITIVE)
    if replay_evidence is not None and not _profit_factor_ok(replay_evidence):
        reasons.append(REPLAY_PROFIT_FACTOR_BELOW_MINIMUM)
    if not _profit_factor_ok(live_evidence):
        reasons.append(LIVE_PROFIT_FACTOR_BELOW_MINIMUM)
    if replay_evidence is not None and replay_evidence.max_drawdown_r > MAX_REPLAY_DRAWDOWN_R:
        reasons.append(REPLAY_DRAWDOWN_ABOVE_MAXIMUM)
    return PromotionReport(
        strategy=identity.strategy,
        registry_key=registry_key,
        identity=identity,
        reasons=tuple(reasons),
        live_sessions=live_sessions,
        live=live_evidence,
        replay=replay_evidence,
        ai=ai_do_no_harm(live, identity=identity),
        replay_note=replay_note,
    )


def _profit_factor_ok(evidence: SampleEvidence) -> bool:
    if not evidence.trades:
        return False
    # No losing trade at all: the factor is unbounded, which passes.
    return evidence.profit_factor is None or evidence.profit_factor >= MIN_PROFIT_FACTOR


# --- reading the evidence ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveEvidence:
    """Everything the live side of the rule reads, grouped by identity."""

    trades: Mapping[str, tuple[EvidenceTrade, ...]] = field(default_factory=dict)
    sessions: Mapping[str, frozenset[date]] = field(default_factory=dict)
    unfilled: Mapping[str, int] = field(default_factory=dict)
    readable: bool = True
    note: str = ""


_LIVE_OUTCOMES = """
    SELECT s.strategy_name, s.strategy_kind, s.session_date, s.hour_bucket, o.filled,
           o.r_multiple, o.cost_r, o.details_json, r.policy_fingerprints_json,
           EXISTS (
               SELECT 1 FROM autotrader_ai_calls a
               WHERE a.run_id = s.run_id AND a.setup_fingerprint = s.setup_fingerprint
                 AND a.status = 'succeeded'
           ),
           EXISTS (
               SELECT 1 FROM autotrader_ai_calls a
               WHERE a.run_id = s.run_id AND a.setup_fingerprint = s.setup_fingerprint
                 AND a.status = 'succeeded' AND a.decision = 'PROPOSE'
           )
    FROM autotrader_manager_signal_outcomes o
    JOIN autotrader_manager_setups s
      ON s.run_id = o.run_id AND s.setup_fingerprint = o.setup_fingerprint
    JOIN autotrader_runs r ON r.id = s.run_id
    WHERE o.fill_model_id = ?
"""
_RUN_SESSIONS = """
    SELECT r.session_date, r.policy_fingerprints_json, v.spec_json
    FROM autotrader_runs r
    JOIN autotrader_strategy_versions v
      ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
    WHERE r.mode = 'shadow'
"""


def _kind_of(strategy_name: str) -> str | None:
    key = REGISTRY_KEYS.get(strategy_name)
    return None if key is None else EVALUATOR_FACTORIES[key]().strategy_kind


def _json_object(raw: object) -> dict[str, Any]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def read_live_evidence(database: str | Path, *, fill_model_id: str = FILL_MODEL_ID) -> LiveEvidence:
    """Live sessions and tracked setups by evidence identity, read-only.

    A database without the tracker's tables reads as no live evidence.
    """
    path = Path(database).expanduser()
    if not path.is_file():
        return LiveEvidence(readable=False, note="no database")
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    trades: dict[str, list[EvidenceTrade]] = defaultdict(list)
    sessions: dict[str, set[date]] = defaultdict(set)
    unfilled: dict[str, int] = defaultdict(int)
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)) as db:
            for session_date, prints_json, spec_json in db.execute(_RUN_SESSIONS):
                name = str(_json_object(spec_json).get("name", "")).strip()
                identity = identity_from_run(name, _kind_of(name), _json_object(prints_json))
                if identity is not None:
                    sessions[identity.fingerprint()].add(date.fromisoformat(str(session_date)))
            tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN"
                    " ('autotrader_manager_setups', 'autotrader_manager_signal_outcomes')"
                )
            }
            rows = db.execute(_LIVE_OUTCOMES, (fill_model_id,)).fetchall() if len(tables) == 2 else []
    except sqlite3.Error as exc:
        return LiveEvidence(readable=False, note=f"{type(exc).__name__}")
    for (
        name, kind, session_date, hour, filled, gross, cost, details_json, prints_json, judged, picked,
    ) in rows:
        identity = identity_from_run(str(name), str(kind), _json_object(prints_json))
        if identity is None:
            continue
        key = identity.fingerprint()
        if not filled:
            unfilled[key] += 1
            continue
        details = _json_object(details_json)
        try:
            exit_at = datetime.fromisoformat(str(details.get("exit_at")))
            trade = EvidenceTrade(
                session_date=date.fromisoformat(str(session_date)),
                exit_at=exit_at,
                net_r=Decimal(str(gross)) - Decimal(str(cost)),
                hour_bucket=int(hour),
                reviewed=bool(judged),
                selected=bool(picked),
            )
        except (TypeError, ValueError, InvalidOperation):
            continue
        trades[key].append(trade)
    return LiveEvidence(
        trades={key: tuple(items) for key, items in trades.items()},
        sessions={key: frozenset(items) for key, items in sessions.items()},
        unfilled=dict(unfilled),
    )


def replay_trades(result: ReplayResult, *, cost_model: CostModel = DEFAULT_COST_MODEL) -> list[EvidenceTrade]:
    """A replay's trades in net R (the same cost model as the live outcomes)."""
    return [
        EvidenceTrade(
            session_date=trade.session_date,
            exit_at=trade.exit_at,
            net_r=trade.r_multiple
            - cost_model.cost_r(entry=trade.entry, stop=trade.stop, exit_reason=trade.exit_reason),
        )
        for trade in result.trades
    ]


def evaluate_registry(
    *,
    replays: Mapping[str, ReplayResult],
    live: LiveEvidence,
    keys: Sequence[str] = tuple(EVALUATOR_FACTORIES),
    replay_notes: Mapping[str, str] | None = None,
    cost_model: CostModel = DEFAULT_COST_MODEL,
) -> tuple[PromotionReport, ...]:
    """One report per registered strategy, under the identity this code runs it with."""
    reports = []
    for key in keys:
        identity = code_identity(key)
        fingerprint = identity.fingerprint()
        replay = replays.get(key)
        reports.append(
            evaluate_promotion(
                identity=identity,
                registry_key=key,
                live=live.trades.get(fingerprint, ()),
                live_sessions=len(live.sessions.get(fingerprint, ())),
                replay=None if replay is None else replay_trades(replay, cost_model=cost_model),
                replay_sessions=0 if replay is None else len(replay.sessions),
                replay_note=(replay_notes or {}).get(key, ""),
            )
        )
    return tuple(reports)


__all__ = [
    "AI_ACTIVE",
    "AI_ADVISORY",
    "AI_INSUFFICIENT",
    "BOOTSTRAP_CONFIDENCE",
    "BOOTSTRAP_RESAMPLES",
    "ELIGIBLE",
    "MAX_REPLAY_DRAWDOWN_R",
    "MIN_LIVE_SETUPS",
    "MIN_MANAGED_SETUPS",
    "MIN_PROFIT_FACTOR",
    "MIN_REPLAY_TRADES",
    "MIN_SESSIONS",
    "NOT_ELIGIBLE",
    "RULE_VERSION",
    "WINDOW_IN_RULES",
    "AIDoNoHarm",
    "BootstrapBound",
    "EvidenceIdentity",
    "EvidenceTrade",
    "LiveEvidence",
    "PromotionReport",
    "SampleEvidence",
    "ai_do_no_harm",
    "code_identity",
    "day_clustered_bootstrap",
    "evaluate_promotion",
    "evaluate_registry",
    "identity_from_run",
    "paired_difference_bootstrap",
    "profit_factor",
    "read_live_evidence",
    "replay_trades",
    "sample_evidence",
    "thresholds",
]
