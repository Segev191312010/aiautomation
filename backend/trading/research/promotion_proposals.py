"""The nightly promoter, Stage 1: propose mode, the promoter's own shadow (T-8).

Owner decision 2026-10-15: promotion builds toward ``auto_within_ceiling`` in
two stages. In Stage 1 the rule below runs every night after the replay and
only PROPOSES. It writes ``proposals.json`` and one line per proposal for the
weekly Telegram summary; the owner decides (a ``DECISION owner:`` ledger line)
and an approved step ships as a reviewed constants commit. Nothing here writes
a constant, a database row or a fence: the module reads, and the nightly job
writes its report file beside the other evidence.

The rule (owner-accepted defaults, OPERATOR_PROMPT Part 8.3), per sendable
identity, over its window (the sessions under its current evidence identity
since its first real order):

* at least ``MIN_TRADES_PER_IDENTITY`` closed real trades and ``MIN_SESSIONS``
  sessions;
* the day-clustered bootstrap lower bound of the expectancy (net R per trade,
  paper-promotion-v1's machinery and confidence) above zero;
* live-vs-shadow slippage at most ``MAX_SLIPPAGE_R_PER_TRADE``: the mean of
  (modelled gross R - real gross R) over the real trades whose setup the
  nightly tracker simulated and filled;
* no unexplained incident in the window: a bracket that needs attention or
  could not be read, or a bridge state file that could not be read (Stage 1
  has no way to explain one, so every incident counts);
* one step at a time on ``SIZE_LADDER`` (0.25 -> 0.5 -> 1.0, never above x1),
  at least ``MIN_SESSIONS_SINCE_STEP`` sessions at the current size;
* GATE: no "up" proposal while the open-risk term (v7c) is not deployed. An
  eligible identity is reported HELD with the token
  ``held_open_risk_term_not_deployed`` instead;
* FENCE (P1-B): no "up" proposal for an identity whose fence is stopped (its
  ``paper:<IDENTITY>`` row, or the global ``paper`` row, in
  ``autotrader_execution_fences``: a Sentinel auto-fence, the nightly replay's
  mismatch fence, an owner's /halt). An eligible fenced identity is reported
  HELD with the token ``held_identity_fenced``, whatever the v7c gate; the
  table is read read-only, and a read error leaves the report unreadable
  (nothing proposed).

A demotion is proposed (never gated) when the evidence turned negative: at
least ``DEMOTION_MIN_TRADES`` real trades and the bootstrap upper bound of the
expectancy below zero (the same one-sided confidence), one rung down, or
shadow-only from the bottom rung.

Size is never part of the evidence: R is quantity-free (``size_history``), so
the window and its numbers run straight through a size step; only "sessions
since the last step" looks at sizes.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from urllib.parse import quote

from trading.research.catalogue import REGISTRY_KEYS
from trading.research.live_ledger import LiveLedger, LiveTrade
from trading.research.paper_promotion import (
    BOOTSTRAP_CONFIDENCE,
    BootstrapBound,
    EvidenceTrade,
    LiveEvidence,
    code_identity,
    day_clustered_bootstrap,
)
from trading.research.replay import FILL_MODEL_ID
from trading.research.size_history import (
    UNKNOWN,
    IdentitySizeHistory,
    SizeSteps,
    effective_multiplier_for,
    read_decision_refs,
    read_size_steps,
)
from trading.risk.identity_sizing import IDENTITY_SIZE_MULTIPLIER
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage.decision_sizing import SizingIndex, read_decision_sizing, sizing_directory
from trading.storage.migrations import SCHEMA_READ_TIMEOUT_SECONDS

RULE_VERSION = "promotion-proposals-v1"
MODE = "propose"
MIN_TRADES_PER_IDENTITY = 30
MIN_SESSIONS = 20
MAX_SLIPPAGE_R_PER_TRADE = Decimal("0.1")
MAX_UNEXPLAINED_INCIDENTS = 0
SIZE_LADDER: tuple[Decimal, ...] = (Decimal("0.25"), Decimal("0.5"), Decimal("1"))
MIN_SESSIONS_SINCE_STEP = 10
PROBATION_SESSIONS = 5
DEMOTION_MIN_TRADES = 10
# Owner decision 2026-10-15: the open-risk term (bridge v7c) gates the first
# step above current sizes. True since the bridge v7c change (plan P3-B1), in the
# same commit that pins that bridge: a tripwire test ties it to the pinned bridge's
# DAY_RISK_BUDGET_PCT, so it can never be True for a bridge without the budget.
OPEN_RISK_TERM_DEPLOYED = True

# Statuses and named tokens.
PROPOSED = "proposed"
HELD = "held"
DEMOTION_PROPOSED = "demotion_proposed"
NOT_ELIGIBLE = "not_eligible"
AT_CEILING = "at_ceiling"
HELD_OPEN_RISK_TERM = "held_open_risk_term_not_deployed"
HELD_IDENTITY_FENCED = "held_identity_fenced"

# The fence table (trading.storage.runtime_fence; not imported, so this module
# loads no write path: a test pins these names equal to its own).
FENCE_TABLE = "autotrader_execution_fences"
GLOBAL_FENCE = "paper"
IDENTITY_FENCE_PREFIX = "paper:"

# Named reasons an identity gets no "up" proposal, in check order.
NOT_REGISTERED = "identity_not_registered"
NOT_ON_LADDER = "current_not_on_ladder"
LIVE_EVIDENCE_UNREADABLE = "live_evidence_unreadable"
TRADES_BELOW_MINIMUM = "trades_below_minimum"
SESSIONS_BELOW_MINIMUM = "sessions_below_minimum"
LOWER_BOUND_NOT_POSITIVE = "expectancy_lower_bound_not_positive"
SLIPPAGE_UNMEASURED = "slippage_unmeasured"
SLIPPAGE_ABOVE_MAXIMUM = "slippage_above_maximum"
UNEXPLAINED_INCIDENTS = "unexplained_incidents"
SESSIONS_SINCE_STEP_BELOW_MINIMUM = "sessions_since_step_below_minimum"

INCIDENT_STATUSES = frozenset({"needs_attention", "unreadable"})
SHADOW_ONLY = "shadow-only"
_R_QUANTUM = Decimal("0.0001")
_TEXT_QUANTUM = Decimal("0.01")
_ZERO = Decimal("0")
_SQL_BATCH = 400


# --- the rule (pure) -------------------------------------------------------------------


def step_up(current: Decimal) -> Decimal | None:
    """The next rung above ``current``: exactly one step, ``None`` at x1 or off the ladder."""
    if current not in SIZE_LADDER:
        return None
    index = SIZE_LADDER.index(current)
    return SIZE_LADDER[index + 1] if index + 1 < len(SIZE_LADDER) else None


def step_down(current: Decimal) -> Decimal | None:
    """One rung below ``current``; ``None`` means shadow-only (below the bottom rung)."""
    lower = [rung for rung in SIZE_LADDER if rung < current]
    return lower[-1] if lower else None


@dataclass(frozen=True, slots=True)
class ShadowOutcome:
    """The nightly tracker's simulation of a real trade's own setup."""

    filled: bool
    gross_r: Decimal | None


@dataclass(frozen=True, slots=True)
class IdentityEvidence:
    """Everything the rule reads for one identity, already restricted to its window."""

    identity: str
    current: Decimal
    evidence_identity: str | None
    window: tuple[date, ...]
    trades: tuple[LiveTrade, ...]
    shadow: Mapping[str, ShadowOutcome]
    incidents: tuple[str, ...]
    first_real_order: date | None
    probation_sessions: int
    size_history: IdentitySizeHistory
    trades_by_multiplier: Mapping[str, int] = field(default_factory=dict)
    live_readable: bool = True
    # The stopped fence's reason (``read_fences``), or None when not fenced.
    fence: str | None = None


@dataclass(frozen=True, slots=True)
class Slippage:
    mean_r: Decimal | None
    pairs: int


def slippage(trades: Sequence[LiveTrade], shadow: Mapping[str, ShadowOutcome]) -> Slippage:
    """Mean (modelled gross R - real gross R) over trades whose setup the model also filled."""
    differences = [
        outcome.gross_r - trade.gross_r
        for trade in trades
        if (outcome := shadow.get(trade.decision_id)) is not None and outcome.filled and outcome.gross_r is not None
    ]
    if not differences:
        return Slippage(None, 0)
    return Slippage(sum(differences, _ZERO) / len(differences), len(differences))


@dataclass(frozen=True, slots=True)
class IdentityProposal:
    identity: str
    status: str
    current: Decimal
    proposed: Decimal | None
    reasons: tuple[str, ...]
    trades: int
    sessions: int
    bound: BootstrapBound
    slippage: Slippage
    incidents: tuple[str, ...]
    sessions_since_last_step: int
    probation_sessions: int
    first_real_order: date | None
    window: tuple[date, ...]
    evidence_identity: str | None
    size_history: IdentitySizeHistory
    trades_by_multiplier: Mapping[str, int] = field(default_factory=dict)
    # Why a HELD identity is held (HELD_IDENTITY_FENCED or HELD_OPEN_RISK_TERM),
    # and its stopped fence's reason, if any.
    hold: str | None = None
    fence: str | None = None

    @property
    def token(self) -> str | None:
        return (self.hold or HELD_OPEN_RISK_TERM) if self.status == HELD else None

    def proposal_line(self) -> str | None:
        """The one Telegram line of a proposal, a held proposal or a demotion; else ``None``."""
        name, current = self.identity, _times(self.current)
        if self.status == PROPOSED:
            return (
                f"PROPOSAL {name}: {current} → {_times(self.proposed)}; {self.trades} trades,"
                f" expectancy {_r(self.bound.estimate)} (lower bound {_r(self.bound.lower)}),"
                f" slippage {_slip(self.slippage.mean_r)}, sessions {self.sessions}"
                f" ({self.sessions_since_last_step} since the last step)"
            )
        if self.status == HELD and self.hold == HELD_IDENTITY_FENCED:
            return f"HELD {name}: eligible for {_times(self.proposed)} but its fence is stopped ({self.fence})"
        if self.status == HELD:
            return f"HELD {name}: eligible for {_times(self.proposed)} but v7c (open-risk term) is not deployed"
        if self.status == DEMOTION_PROPOSED:
            target = SHADOW_ONLY if self.proposed is None else _times(self.proposed)
            return (
                f"DEMOTION PROPOSAL {name}: {current} → {target}; {self.trades} trades,"
                f" expectancy {_r(self.bound.estimate)} (upper bound {_r(self.bound.upper)}),"
                f" slippage {_slip(self.slippage.mean_r)}, sessions {self.sessions}"
            )
        return None

    def status_line(self) -> str:
        """Per identity: its size, its probation and where it stands."""
        probation = f"probation {min(self.probation_sessions, PROBATION_SESSIONS)}/{PROBATION_SESSIONS}"
        if self.first_real_order is None:
            probation += " (no real order yet)"
        verdict = {
            PROPOSED: "proposal above",
            HELD: "held (fenced)" if self.hold == HELD_IDENTITY_FENCED else "held (v7c)",
            DEMOTION_PROPOSED: "demotion proposed",
            AT_CEILING: f"at the {_times(SIZE_LADDER[-1])} ceiling",
        }.get(self.status)
        if verdict is None:
            texts = [text for reason in self.reasons if (text := self._reason_text(reason))]
            verdict = "not eligible: " + ", ".join(texts or self.reasons)
        if self.fence is not None and self.hold != HELD_IDENTITY_FENCED:
            verdict += f"; fenced ({self.fence})"
        return f"{self.identity} {_times(self.current)}: {probation}; {verdict}"

    def _reason_text(self, reason: str) -> str | None:
        texts: dict[str, str | None] = {
            NOT_REGISTERED: "not a registered strategy",
            NOT_ON_LADDER: f"{_times(self.current)} is not on the ladder",
            LIVE_EVIDENCE_UNREADABLE: "live evidence unreadable",
            TRADES_BELOW_MINIMUM: f"trades {self.trades}/{MIN_TRADES_PER_IDENTITY}",
            SESSIONS_BELOW_MINIMUM: f"sessions {self.sessions}/{MIN_SESSIONS}",
            LOWER_BOUND_NOT_POSITIVE: (
                None if self.bound.lower is None else f"expectancy lower bound {_r(self.bound.lower)}"
            ),
            SLIPPAGE_UNMEASURED: "slippage unmeasured",
            SLIPPAGE_ABOVE_MAXIMUM: (
                f"slippage {_slip(self.slippage.mean_r)} > {_slip(MAX_SLIPPAGE_R_PER_TRADE)}"
            ),
            UNEXPLAINED_INCIDENTS: f"{len(self.incidents)} unexplained incident(s)",
            SESSIONS_SINCE_STEP_BELOW_MINIMUM: (
                f"{self.sessions_since_last_step}/{MIN_SESSIONS_SINCE_STEP} sessions since the last step"
            ),
        }
        return texts.get(reason, reason)

    def payload(self) -> dict[str, object]:
        return {
            "identity": self.identity,
            "status": self.status,
            "token": self.token,
            "fence": self.fence,
            "current": _plain(self.current),
            "proposed": None if self.proposed is None else _plain(self.proposed),
            "proposed_text": (
                SHADOW_ONLY if self.status == DEMOTION_PROPOSED and self.proposed is None else None
            ),
            "reasons": list(self.reasons),
            "evidence": {
                "trades": self.trades,
                "sessions": self.sessions,
                "expectancy_r": _text(self.bound.estimate),
                "expectancy_lower_bound_r": _text(self.bound.lower),
                "expectancy_upper_bound_r": _text(self.bound.upper),
                "bootstrap_confidence": str(BOOTSTRAP_CONFIDENCE),
                "bootstrap_days": self.bound.days,
                "slippage_r_per_trade": _text(self.slippage.mean_r),
                "slippage_pairs": self.slippage.pairs,
                "unexplained_incidents": list(self.incidents),
                "sessions_since_last_step": self.sessions_since_last_step,
                "trades_by_multiplier": dict(self.trades_by_multiplier),
            },
            "window": {
                "evidence_identity": self.evidence_identity,
                "first_session": self.window[0].isoformat() if self.window else None,
                "last_session": self.window[-1].isoformat() if self.window else None,
                "sessions": len(self.window),
            },
            "probation": {
                "sessions": min(self.probation_sessions, PROBATION_SESSIONS),
                "of": PROBATION_SESSIONS,
                "complete": self.probation_sessions >= PROBATION_SESSIONS,
                "first_real_order": None if self.first_real_order is None else self.first_real_order.isoformat(),
            },
            "line": self.proposal_line(),
            "status_line": self.status_line(),
        }


def evaluate_identity(evidence: IdentityEvidence, *, open_risk_term_deployed: bool | None = None) -> IdentityProposal:
    """Apply the rule to one identity (pure: nothing is read or written)."""
    gate_open = OPEN_RISK_TERM_DEPLOYED if open_risk_term_deployed is None else open_risk_term_deployed
    current = evidence.current
    trades = evidence.trades
    sample = [
        EvidenceTrade(
            session_date=trade.session_date,
            exit_at=datetime.combine(trade.session_date, time(), tzinfo=timezone.utc),
            net_r=trade.net_r,
        )
        for trade in trades
    ]
    seed = f"{evidence.evidence_identity or evidence.identity}:{RULE_VERSION}"
    bound = day_clustered_bootstrap(sample, seed_label=seed)
    slip = slippage(trades, evidence.shadow)
    since = evidence.size_history.sessions_since_last_step(current)
    sessions = len(evidence.window)

    reasons: list[str] = []
    if evidence.evidence_identity is None:
        reasons.append(NOT_REGISTERED)
    if current not in SIZE_LADDER:
        reasons.append(NOT_ON_LADDER)
    if not evidence.live_readable:
        reasons.append(LIVE_EVIDENCE_UNREADABLE)
    if len(trades) < MIN_TRADES_PER_IDENTITY:
        reasons.append(TRADES_BELOW_MINIMUM)
    if sessions < MIN_SESSIONS:
        reasons.append(SESSIONS_BELOW_MINIMUM)
    if bound.lower is None or bound.lower <= 0:
        reasons.append(LOWER_BOUND_NOT_POSITIVE)
    if slip.mean_r is None:
        reasons.append(SLIPPAGE_UNMEASURED)
    elif slip.mean_r > MAX_SLIPPAGE_R_PER_TRADE:
        reasons.append(SLIPPAGE_ABOVE_MAXIMUM)
    if len(evidence.incidents) > MAX_UNEXPLAINED_INCIDENTS:
        reasons.append(UNEXPLAINED_INCIDENTS)
    if since < MIN_SESSIONS_SINCE_STEP:
        reasons.append(SESSIONS_SINCE_STEP_BELOW_MINIMUM)

    negative = len(trades) >= DEMOTION_MIN_TRADES and bound.upper is not None and bound.upper < 0
    up = step_up(current)
    hold: str | None = None
    if negative:
        status, proposed = DEMOTION_PROPOSED, step_down(current)
    elif current in SIZE_LADDER and up is None:
        status, proposed = AT_CEILING, None
    elif reasons:
        status, proposed = NOT_ELIGIBLE, None
    elif evidence.fence is not None:
        # Eligible, but stopped: a fenced identity sends nothing, so it earns no
        # step up while its fence stands, whatever the v7c gate.
        status, proposed, hold = HELD, up, HELD_IDENTITY_FENCED
    else:
        # Eligible: exactly one rung up, and only once v7c is deployed.
        status, proposed = (PROPOSED if gate_open else HELD), up
        hold = None if gate_open else HELD_OPEN_RISK_TERM
    return IdentityProposal(
        identity=evidence.identity,
        status=status,
        current=current,
        proposed=proposed,
        reasons=tuple(reasons),
        trades=len(trades),
        sessions=sessions,
        bound=bound,
        slippage=slip,
        incidents=evidence.incidents,
        sessions_since_last_step=since,
        probation_sessions=evidence.probation_sessions,
        first_real_order=evidence.first_real_order,
        window=evidence.window,
        evidence_identity=evidence.evidence_identity,
        size_history=evidence.size_history,
        trades_by_multiplier=evidence.trades_by_multiplier,
        hold=hold,
        fence=evidence.fence,
    )


# --- the report --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromotionProposals:
    as_of: date
    identities: tuple[IdentityProposal, ...] = ()
    size_steps: SizeSteps = field(default_factory=SizeSteps)
    readable: bool = True
    note: str = ""

    def lines(self) -> list[str]:
        """The weekly summary's lines: proposals first, then each identity's standing."""
        lines = [f"Promotion proposals ({RULE_VERSION}, propose mode: nothing changes until the owner approves)"]
        if not self.readable:
            lines.append(f"not evaluated: {self.note}")
            return lines
        lines += [line for item in self.identities if (line := item.proposal_line())]
        lines += [item.status_line() for item in self.identities]
        return lines

    def payload(self, *, generated_at: datetime) -> dict[str, object]:
        return {
            "schema": "autotrader-v1/promotion-proposals",
            "version": 1,
            "rule": RULE_VERSION,
            "mode": MODE,
            "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
            "as_of": self.as_of.isoformat(),
            "grants": (
                "nothing: a proposal changes no constant, database row or fence;"
                " an approved step ships as a reviewed constants commit"
            ),
            "open_risk_term_deployed": OPEN_RISK_TERM_DEPLOYED,
            "readable": self.readable,
            "note": self.note,
            "thresholds": thresholds(),
            "identities": [item.payload() for item in self.identities],
            "lines": self.lines(),
        }


def thresholds() -> dict[str, object]:
    return {
        "min_trades_per_identity": MIN_TRADES_PER_IDENTITY,
        "min_sessions": MIN_SESSIONS,
        "expectancy": "day-clustered bootstrap lower bound > 0 (paper-promotion-v1)",
        "bootstrap_confidence_one_sided": str(BOOTSTRAP_CONFIDENCE),
        "max_slippage_r_per_trade": str(MAX_SLIPPAGE_R_PER_TRADE),
        "max_unexplained_incidents": MAX_UNEXPLAINED_INCIDENTS,
        "size_ladder": [_plain(rung) for rung in SIZE_LADDER],
        "min_sessions_since_step": MIN_SESSIONS_SINCE_STEP,
        "probation_sessions": PROBATION_SESSIONS,
        "demotion": f"at least {DEMOTION_MIN_TRADES} trades and the bootstrap upper bound < 0",
    }


# --- gathering one identity's evidence (read-only) -----------------------------------


def _file_date(name: str) -> date | None:
    try:
        return date.fromisoformat(name.removesuffix(".json"))
    except ValueError:
        return None


def identity_evidence(
    identity: str,
    *,
    current: Decimal,
    live: LiveEvidence,
    ledger: LiveLedger,
    shadow: Mapping[str, ShadowOutcome],
    size_history: IdentitySizeHistory,
    multipliers: Mapping[str, str] | None = None,
    fence: str | None = None,
) -> IdentityEvidence:
    """The identity's window: sessions under its current evidence identity since its first real order."""
    key = REGISTRY_KEYS.get(identity)
    evidence_identity = None if key is None else code_identity(key).fingerprint()
    orders = sorted(item.session_date for item in ledger.brackets if item.strategy == identity)
    first = orders[0] if orders else None
    identity_sessions = live.sessions.get(evidence_identity, frozenset()) if evidence_identity else frozenset()
    window = tuple(sorted(day for day in identity_sessions if first is not None and day >= first))
    in_window = set(window)
    trades = tuple(trade for trade in ledger.trades if trade.strategy == identity and trade.session_date in in_window)
    incidents = [
        f"{item.session_date.isoformat()}:{item.decision_id}:{item.status}"
        for item in ledger.brackets
        if item.strategy == identity and item.status in INCIDENT_STATUSES and item.session_date in in_window
    ]
    # A bridge state file that could not be read hides every identity's brackets that day.
    incidents += [
        f"{name}:file_unreadable"
        for name in ledger.unreadable
        if ":" not in name and (day := _file_date(name)) is not None and day in in_window
    ]
    counted = Counter((multipliers or {}).get(trade.decision_id, UNKNOWN) for trade in trades)
    return IdentityEvidence(
        identity=identity,
        current=current,
        evidence_identity=evidence_identity,
        window=window,
        trades=trades,
        shadow={trade.decision_id: shadow[trade.decision_id] for trade in trades if trade.decision_id in shadow},
        incidents=tuple(incidents),
        first_real_order=first,
        probation_sessions=0 if first is None else sum(1 for day in ledger.sessions if day >= first),
        size_history=size_history,
        trades_by_multiplier=dict(sorted(counted.items())),
        live_readable=live.readable,
        fence=fence,
    )


_SHADOW = """
    SELECT d.id, o.filled, o.r_multiple
    FROM autotrader_candidate_decisions d
    JOIN autotrader_manager_signal_outcomes o
      ON o.run_id = d.run_id AND o.setup_fingerprint = d.setup_fingerprint AND o.fill_model_id = ?
    WHERE d.id IN ({marks})
"""


def read_shadow_outcomes(
    database: str | Path, decision_ids: Sequence[str], *, fill_model_id: str = FILL_MODEL_ID
) -> dict[str, ShadowOutcome]:
    """The tracker's outcome of each real trade's own setup (read-only; none without the table)."""
    path = Path(database).expanduser()
    wanted = sorted(set(decision_ids))
    if not wanted or not path.is_file():
        return {}
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    outcomes: dict[str, ShadowOutcome] = {}
    with closing(sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)) as db:
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"autotrader_candidate_decisions", "autotrader_manager_signal_outcomes"} <= tables:
            return {}
        for start in range(0, len(wanted), _SQL_BATCH):
            batch = wanted[start : start + _SQL_BATCH]
            marks = ",".join("?" for _ in batch)
            for decision_id, filled, gross in db.execute(_SHADOW.format(marks=marks), (fill_model_id, *batch)):
                try:
                    value = None if gross is None else Decimal(str(gross))
                except InvalidOperation:
                    continue
                outcomes[str(decision_id)] = ShadowOutcome(filled=bool(filled), gross_r=value)
    return outcomes


def _stopped(value: object) -> bool:
    """A fence row's ``stopped``; a value that is not a number reads as stopped."""
    try:
        return int(str(value)) != 0
    except ValueError:
        return True


def read_fences(database: str | Path) -> dict[str, str]:
    """Every stopped fence's reason by identity (read-only): each stopped
    ``paper:<IDENTITY>`` row, and every sendable identity while the global
    ``paper`` row is stopped. No database or no fence table: nothing is fenced
    (the same convention as ``read_shadow_outcomes``); a read error raises."""
    path = Path(database).expanduser()
    if not path.is_file():
        return {}
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)) as db:
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if FENCE_TABLE not in tables:
            return {}
        rows = db.execute(
            f"SELECT name, stopped, reason FROM {FENCE_TABLE} WHERE name = ? OR name LIKE ? ORDER BY name",
            (GLOBAL_FENCE, f"{IDENTITY_FENCE_PREFIX}%"),
        ).fetchall()
    fences: dict[str, str] = {}
    stopped_globally: str | None = None
    for name, stopped, reason in rows:
        if not _stopped(stopped):
            continue
        if str(name) == GLOBAL_FENCE:
            stopped_globally = f"global PAPER stop: {reason}"
        else:
            fences[str(name)[len(IDENTITY_FENCE_PREFIX):]] = str(reason)
    if stopped_globally is not None:
        for identity in SENDABLE_IDENTITIES:
            fences.setdefault(identity, stopped_globally)
    return fences


def collect_proposals(
    *,
    database: str | Path,
    ledger: LiveLedger,
    live: LiveEvidence,
    as_of: date,
    sizing: SizingIndex | None = None,
    current: Mapping[str, Decimal] | None = None,
    open_risk_term_deployed: bool | None = None,
    fences: Mapping[str, str] | None = None,
) -> PromotionProposals:
    """The nightly call: every sendable identity under the rule, read-only.

    ``fences`` (tests) replaces the read of the fence table (``read_fences``).
    """
    sizes = {name: IDENTITY_SIZE_MULTIPLIER[name] for name in SENDABLE_IDENTITIES} if current is None else dict(current)
    index = read_decision_sizing(sizing_directory(database)) if sizing is None else sizing
    steps = read_size_steps(database, index, current=sizes)
    decision_ids = [trade.decision_id for trade in ledger.trades if trade.strategy in sizes]
    try:
        shadow = read_shadow_outcomes(database, decision_ids)
        refs = read_decision_refs(database, decision_ids=decision_ids)
        # Unreadable fences leave the whole report unreadable: nothing is proposed.
        fenced = read_fences(database) if fences is None else dict(fences)
    except sqlite3.Error as exc:
        return PromotionProposals(as_of=as_of, size_steps=steps, readable=False, note=type(exc).__name__)
    multipliers = {}
    for decision_id in decision_ids:
        ref = refs.get(decision_id)
        if ref is None:
            continue
        value = effective_multiplier_for(ref, recorded=index.records).value
        multipliers[decision_id] = UNKNOWN if value is None else _plain(value)
    identities = tuple(
        evaluate_identity(
            identity_evidence(
                name,
                current=size,
                live=live,
                ledger=ledger,
                shadow=shadow,
                size_history=steps.histories.get(name, IdentitySizeHistory(identity=name)),
                multipliers=multipliers,
                fence=fenced.get(name),
            ),
            open_risk_term_deployed=open_risk_term_deployed,
        )
        for name, size in sizes.items()
    )
    return PromotionProposals(as_of=as_of, identities=identities, size_steps=steps)


# --- text helpers ----------------------------------------------------------------------


def _plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if Decimal(text) == 0 else text


def _times(value: Decimal | None) -> str:
    return "×?" if value is None else f"×{_plain(value)}"


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(_R_QUANTUM, rounding=ROUND_HALF_EVEN))


def _r(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(_TEXT_QUANTUM, rounding=ROUND_HALF_EVEN):+}R"


def _slip(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(_TEXT_QUANTUM, rounding=ROUND_HALF_EVEN)}R/trade"


__all__ = [
    "AT_CEILING",
    "DEMOTION_MIN_TRADES",
    "DEMOTION_PROPOSED",
    "FENCE_TABLE",
    "GLOBAL_FENCE",
    "HELD",
    "HELD_IDENTITY_FENCED",
    "HELD_OPEN_RISK_TERM",
    "IDENTITY_FENCE_PREFIX",
    "MAX_SLIPPAGE_R_PER_TRADE",
    "MAX_UNEXPLAINED_INCIDENTS",
    "MIN_SESSIONS",
    "MIN_SESSIONS_SINCE_STEP",
    "MIN_TRADES_PER_IDENTITY",
    "MODE",
    "NOT_ELIGIBLE",
    "OPEN_RISK_TERM_DEPLOYED",
    "PROBATION_SESSIONS",
    "PROPOSED",
    "RULE_VERSION",
    "SIZE_LADDER",
    "IdentityEvidence",
    "IdentityProposal",
    "PromotionProposals",
    "ShadowOutcome",
    "Slippage",
    "collect_proposals",
    "evaluate_identity",
    "identity_evidence",
    "read_fences",
    "read_shadow_outcomes",
    "slippage",
    "step_down",
    "step_up",
    "thresholds",
]
