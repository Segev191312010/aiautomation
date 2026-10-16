"""The nightly evidence run that follows the full replay (plan Design F).

``autotraderctl research replay`` is the supervisor's run-once replay child
(17:35-18:30 ET). When it replays every registered strategy it then gathers
the evidence for the paper promotion rule, in this order:

1. the signal-outcome tracker: every saved setup simulated forward on the
   recorded bars (its own append-only table is the only database write);
2. the live R ledger from the PAPER bridge's state files (read-only);
3. the paper promotion rule for every registered strategy (reports only),
   then the promoter in propose mode for every sendable identity
   (``trading.research.promotion_proposals``: proposals only, it changes no
   constant, row or fence) with each identity's size steps;
4. live/shadow decision parity (T-9): a sendable identity whose live
   decisions the replay does not reproduce is fenced (``paper:<IDENTITY>``
   stopped=1, reason ``sentinel: replay mismatch <n> decisions``) through the
   Sentinel's own ``fence_identity``, and the result is recorded in
   ``evidence.json``; the notifier announces the fence at its 07:55 start;
5. the window research sweep, unless it would start after
   ``SWEEP_LATEST_START`` inside the replay window: the supervisor stops the
   child at 18:30, and the weekly message must not be cut off with it.

Step 4 is a hook today: nothing yet replays a live run's decisions (see
``PARITY_NOT_COMPARED_NOTE``), so ``NightlyEnvironment.compare_decisions`` is
None and the report says ``not_compared``. A comparer, once one exists,
returns one ``trading.replay.ReplayEqualityReport`` per identity. Only a
decision the replay made differently (``fingerprint_mismatch``) or made
without a recorded one (``unexpected_in_replay``) is a mismatch; a recorded
decision the replay lacks (``missing_from_replay``: stored bars incomplete)
is missing data, reported as ``not_compared`` for its identity and never
fenced (N15a LOW-3). The fence is written only to the database the promoter
reads its fences from, ``fence_database`` (N15a LOW-4).

The reports land next to the replay's (``evidence.json``, ``windows.json``,
``live-r.json``, ``proposals.json``, ``evidence.txt``). Nothing here can place
an order or change an authority: the only write besides the tracker's table is
the stop-only identity fence; a database that is not this code's schema is
left alone.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path

from trading.operations.paths import atomic_private_write
from trading.operations.sentinel import (
    FENCED,
    FenceStore,
    fence_identity,
    replay_mismatch_reason,
)
from trading.replay.equality import ReplayEqualityReport
from trading.research.bars_store import NEW_YORK, BarsStore, ResearchPaths
from trading.research.live_ledger import LiveLedger, bridge_state_directory, read_live_ledger
from trading.research.outcomes import TrackerReport, track_outcomes
from trading.research.overlay import OverlayScorecard, read_scorecard
from trading.research.paper_promotion import (
    LiveEvidence,
    PromotionReport,
    evaluate_registry,
    read_live_evidence,
)
from trading.research.promotion_proposals import PromotionProposals, collect_proposals
from trading.research.replay import FILL_MODEL_ID, ReplayResult, SkippedStrategy
from trading.research.windows import FamilyResearch, research_payload, sweep_all
from trading.scheduler.sessions import market_session
from trading.sendable_identities import SENDABLE_IDENTITIES

logger = logging.getLogger("autotrader.shadow.research")

UTC = timezone.utc
# Inside the supervised replay window (17:35-18:30 ET) the sweep starts no
# later than this; outside it (a manual run) it always runs.
REPLAY_WINDOW = (time(17, 35), time(18, 30))
SWEEP_LATEST_START = time(18, 0)
_R_QUANTUM = Decimal("0.0001")

Clock = Callable[[], datetime]
# Per identity, the replay's equality report against its live run (T-9 hook).
DecisionComparer = Callable[[Sequence[ReplayResult]], Mapping[str, ReplayEqualityReport]]

PARITY_NOT_COMPARED = "not_compared"
PARITY_COMPARED = "compared"
# The fence store's database is not the evidence database: nothing is written.
PARITY_FENCE_DATABASE_MISMATCH = "failed_fence_database_mismatch"
# The decision mismatches that fence (trading.replay.equality's reasons). A
# recorded decision the replay lacks is missing data, never a mismatch.
DECISION_MISMATCH_REASONS = frozenset({"fingerprint_mismatch", "unexpected_in_replay"})
MISSING_FROM_REPLAY = "missing_from_replay"
# What is missing before step 4 can compare anything (2026-10-15): the
# research replay walks stored bars with synthetic quotes over every stored
# contract and never reads the live decisions, and nothing re-evaluates a
# live run's candidates on their persisted bars
# (``trading.replay.PersistedReplayRepository``) to build the replayed
# ``ReplayFingerprintSet`` that ``compare_replay_fingerprints`` needs.
PARITY_NOT_COMPARED_NOTE = (
    "live decisions are not replayed yet: no replayed decision fingerprints to compare"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class NightlyEnvironment:
    """Where the evidence run reads and writes; tests substitute every path."""

    database: Path
    bridge_directory: Path
    clock: Clock = _utc_now
    # The live/shadow decision comparison (None: not built yet) and the fence
    # store it writes through (None: ``SQLitePaperKillStore`` on the configured
    # database, the one ``current()`` names).
    compare_decisions: DecisionComparer | None = None
    fence_store: FenceStore | None = None

    @classmethod
    def current(cls) -> NightlyEnvironment:
        from config import cfg

        return cls(database=Path(cfg.DB_PATH).expanduser(), bridge_directory=bridge_state_directory())


def fence_database(environment: NightlyEnvironment) -> Path:
    """The one database file of the nightly fences (N15a LOW-4): the promoter reads
    them from it, and the replay-mismatch fence is written only when the fence
    store's database is this file, so a fence written tonight holds tonight's
    proposal for that identity."""
    return Path(environment.database).expanduser().resolve()


def _store_database() -> Path:
    """The file ``SQLitePaperKillStore`` writes: ``db.core`` opens ``cfg.DB_PATH``
    as given (no ``~`` expansion), relative to the working directory."""
    from config import cfg

    return Path(cfg.DB_PATH).resolve()


@dataclass(frozen=True, slots=True)
class LiveHour:
    hour: int
    trades: int
    total_r: Decimal

    @property
    def expectancy_r(self) -> Decimal:
        return self.total_r / self.trades


@dataclass(frozen=True, slots=True)
class DecisionParity:
    """Step 4's outcome: mismatched decisions per identity and what was fenced."""

    status: str
    mismatches: Mapping[str, int] = field(default_factory=dict)
    fenced: tuple[str, ...] = ()
    already_fenced: tuple[str, ...] = ()
    failed: Mapping[str, str] = field(default_factory=dict)
    note: str = ""
    # Recorded decisions the replay lacks, per identity: missing data, not compared.
    not_compared: Mapping[str, int] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mismatches": dict(self.mismatches),
            "fenced": list(self.fenced),
            "already_fenced": list(self.already_fenced),
            "failed": dict(self.failed),
            "note": self.note,
            "not_compared": dict(self.not_compared),
        }


def _not_compared() -> DecisionParity:
    return DecisionParity(status=PARITY_NOT_COMPARED, note=PARITY_NOT_COMPARED_NOTE)


def _decision_counts(reports: Mapping[str, ReplayEqualityReport], reasons: frozenset[str]) -> dict[str, int]:
    counts = {
        identity: sum(1 for item in report.mismatches if item.category == "decisions" and item.reason in reasons)
        for identity, report in reports.items()
    }
    return {identity: count for identity, count in sorted(counts.items()) if count > 0}


def decision_mismatches(reports: Mapping[str, ReplayEqualityReport]) -> dict[str, int]:
    """Mismatched decisions per identity: the ``decisions`` category, and only a
    decision made differently or made without a recorded one; never one the
    replay lacks (missing data is never a mismatch)."""
    return _decision_counts(reports, DECISION_MISMATCH_REASONS)


def decisions_not_compared(reports: Mapping[str, ReplayEqualityReport]) -> dict[str, int]:
    """Recorded decisions per identity that the replay lacks (``missing_from_replay``)."""
    return _decision_counts(reports, frozenset({MISSING_FROM_REPLAY}))


async def fence_replay_mismatches(
    mismatches: Mapping[str, int], *, store: FenceStore
) -> DecisionParity:
    """Fence every sendable identity with a mismatched decision (stop-only).

    A shadow identity sends nothing, so its mismatch is reported, never
    fenced; an identity already stopped keeps its row and reason.
    """
    fenced: list[str] = []
    already: list[str] = []
    failed: dict[str, str] = {}
    counted = {identity: count for identity, count in sorted(mismatches.items()) if count > 0}
    for identity, count in counted.items():
        if identity not in SENDABLE_IDENTITIES:
            continue
        reason = replay_mismatch_reason(count)
        try:
            outcome = await fence_identity(store, identity=identity, reason=reason)
        except Exception as exc:  # noqa: BLE001 - named in the report; the others still fence
            logger.exception("replay mismatch fence for %s NOT written", identity)
            failed[identity] = _failed(exc)
            continue
        if outcome == FENCED:
            logger.critical("fenced %s: %s", identity, reason)
            fenced.append(identity)
        else:
            logger.warning("%s already fenced; %s", identity, reason)
            already.append(identity)
    return DecisionParity(
        status=PARITY_COMPARED,
        mismatches=counted,
        fenced=tuple(fenced),
        already_fenced=tuple(already),
        failed=failed,
    )


def check_decision_parity(
    results: Sequence[ReplayResult], environment: NightlyEnvironment
) -> DecisionParity:
    """Step 4: compare (when a comparer exists) and fence what does not match."""
    if environment.compare_decisions is None:
        return _not_compared()
    reports = environment.compare_decisions(results)
    mismatches = decision_mismatches(reports)
    missing = decisions_not_compared(reports)
    if not mismatches:
        return DecisionParity(status=PARITY_COMPARED, not_compared=missing)
    store = environment.fence_store
    if store is None:
        from trading.storage.runtime_fence import SQLitePaperKillStore

        if _store_database() != fence_database(environment):
            # A fence the promoter would not read is no fence: refuse, loudly.
            logger.error(
                "replay mismatch fences NOT written: the fence store's database is not %s",
                fence_database(environment).name,
            )
            return DecisionParity(
                status=PARITY_FENCE_DATABASE_MISMATCH, mismatches=mismatches, not_compared=missing
            )
        store = SQLitePaperKillStore()
    parity = asyncio.run(fence_replay_mismatches(mismatches, store=store))
    return replace(parity, not_compared=missing)


@dataclass(frozen=True, slots=True)
class NightlyEvidence:
    tracker: TrackerReport
    promotion: tuple[PromotionReport, ...]
    live_r: LiveLedger
    windows: tuple[FamilyResearch, ...] = ()
    windows_note: str = ""
    # Live (tracked) setups of the current identity by signal hour, per strategy.
    live_hours: Mapping[str, tuple[LiveHour, ...]] = field(default_factory=dict)
    week_start: date | None = None
    # Managed MOMO against MOMO_HOD_V2 on the same setups (phase-3 readiness).
    phase3: OverlayScorecard | None = None
    # The promoter's proposals (propose mode) and each sendable identity's size steps.
    proposals: PromotionProposals | None = None
    parity: DecisionParity = field(default_factory=_not_compared)


def week_start(day: date) -> date:
    """The first XNYS session of ``day``'s ISO week."""
    monday = day - timedelta(days=day.weekday())
    for offset in range(7):
        candidate = monday + timedelta(days=offset)
        if market_session(candidate) is not None:
            return candidate
    return monday


def sweep_allowed(now: datetime) -> tuple[bool, str]:
    local = now.astimezone(NEW_YORK).time()
    if REPLAY_WINDOW[0] <= local < REPLAY_WINDOW[1] and local > SWEEP_LATEST_START:
        return False, (
            f"not swept tonight: started after {SWEEP_LATEST_START:%H:%M} ET"
            f" (the replay window ends {REPLAY_WINDOW[1]:%H:%M})"
        )
    return True, ""


def _live_hours(promotion: Sequence[PromotionReport], live: LiveEvidence) -> dict[str, tuple[LiveHour, ...]]:
    hours: dict[str, tuple[LiveHour, ...]] = {}
    for report in promotion:
        trades = live.trades.get(report.identity.fingerprint(), ())
        grouped: dict[int, list[Decimal]] = defaultdict(list)
        for trade in trades:
            if trade.hour_bucket is not None:
                grouped[trade.hour_bucket].append(trade.net_r)
        if grouped:
            hours[report.strategy] = tuple(
                LiveHour(hour=hour, trades=len(values), total_r=sum(values, Decimal("0")))
                for hour, values in sorted(grouped.items())
            )
    return hours


def _failed(exc: BaseException) -> str:
    return f"failed_{type(exc).__name__.lower()}"[:64]


def collect_evidence(
    results: Sequence[ReplayResult],
    skipped: Sequence[SkippedStrategy],
    store: BarsStore,
    *,
    environment: NightlyEnvironment,
) -> NightlyEvidence:
    """Tracker, live R, promotion and (time permitting) the window sweep.

    Each step stands alone: one that fails is logged and named in the
    reports, and the others (and the replay before them) still count. A
    SIGTERM (a BaseException) is never caught here.
    """
    now = environment.clock()
    try:
        tracker = asyncio.run(track_outcomes(store=store, database=environment.database, now=now))
    except Exception as exc:  # noqa: BLE001 - logged and reported; the replay stands
        logger.exception("signal-outcome tracker failed")
        tracker = TrackerReport(status="skipped", fill_model_id=FILL_MODEL_ID, reason=_failed(exc))
    try:
        live_r = read_live_ledger(environment.bridge_directory)
    except Exception as exc:  # noqa: BLE001 - logged and reported; the replay stands
        logger.exception("live R ledger failed")
        live_r = LiveLedger(unreadable=(_failed(exc),))
    try:
        live = read_live_evidence(environment.database)
        replays = {result.strategy: result for result in results}
        notes = {item.strategy: item.note for item in skipped}
        promotion = evaluate_registry(replays=replays, live=live, replay_notes=notes)
    except Exception:  # noqa: BLE001 - logged and reported; the replay stands
        logger.exception("paper promotion evaluation failed")
        live, promotion = LiveEvidence(readable=False, note="failed"), ()
    try:
        parity = check_decision_parity(results, environment)
    except Exception as exc:  # noqa: BLE001 - logged and reported; the replay stands
        logger.exception("decision parity failed")
        parity = DecisionParity(status=_failed(exc))
    as_of = now.astimezone(NEW_YORK).date()
    try:
        # After step 4 on purpose: a mismatch fence written tonight holds
        # tonight's proposal for that identity (the promoter reads the fences).
        proposals = collect_proposals(database=fence_database(environment), ledger=live_r, live=live, as_of=as_of)
    except Exception as exc:  # noqa: BLE001 - logged and reported; the replay stands
        logger.exception("promotion proposals failed")
        proposals = PromotionProposals(as_of=as_of, readable=False, note=_failed(exc))
    try:
        # Every managed session so far: phase 3 is a cumulative question.
        phase3 = read_scorecard(environment.database, end=as_of, days=None)
    except Exception as exc:  # noqa: BLE001 - logged and reported; the replay stands
        logger.exception("overlay scorecard failed")
        phase3 = OverlayScorecard(start=None, end=as_of, readable=False, note=_failed(exc))
    allowed, note = sweep_allowed(environment.clock())
    windows: tuple[FamilyResearch, ...] = ()
    if allowed:
        try:
            windows = sweep_all(store)
        except Exception as exc:  # noqa: BLE001 - logged and reported; the replay stands
            logger.exception("window research failed")
            note = f"not swept tonight: {_failed(exc)}"
    else:
        logger.warning("window research %s", note)
    return NightlyEvidence(
        tracker=tracker,
        promotion=promotion,
        live_r=live_r,
        windows=windows,
        windows_note=note,
        live_hours=_live_hours(promotion, live),
        week_start=week_start(as_of),
        phase3=phase3,
        proposals=proposals,
        parity=parity,
    )


def write_evidence(
    evidence: NightlyEvidence,
    *,
    paths: ResearchPaths,
    as_of: date,
    generated_at: datetime,
    text: str,
) -> tuple[str, ...]:
    """Private JSON reports plus the plain-text evidence summary, beside the replay's."""
    directory = paths.ensure_private(paths.reports_directory / as_of.isoformat())
    stamp = generated_at.astimezone(UTC).isoformat()
    documents = {
        "evidence.json": {
            "schema": "autotrader-v1/research-evidence",
            "version": 1,
            "generated_at": stamp,
            "tracker": evidence.tracker.to_payload(),
            "promotion": [report.payload() for report in evidence.promotion],
            "live_hours": {
                name: [
                    {"hour": row.hour, "trades": row.trades, "total_r": str(row.total_r.quantize(_R_QUANTUM))}
                    for row in rows
                ]
                for name, rows in evidence.live_hours.items()
            },
            "phase3": None if evidence.phase3 is None else evidence.phase3.payload(),
            "size_steps": None if evidence.proposals is None else evidence.proposals.size_steps.payload(),
            "decision_parity": evidence.parity.payload(),
        },
        "windows.json": {
            "schema": "autotrader-v1/research-windows",
            "version": 1,
            "generated_at": stamp,
            "note": evidence.windows_note,
            "families": [research_payload(item) for item in evidence.windows],
        },
        "live-r.json": {
            "schema": "autotrader-v1/research-live-r",
            "version": 1,
            "generated_at": stamp,
            **evidence.live_r.payload(),
        },
    }
    if evidence.proposals is not None:
        documents["proposals.json"] = evidence.proposals.payload(generated_at=generated_at)
    written = []
    for name, document in documents.items():
        target = directory / name
        atomic_private_write(target, json.dumps(document, indent=2).encode("utf-8"))
        written.append(str(target))
    summary = directory / "evidence.txt"
    atomic_private_write(summary, (text + "\n").encode("utf-8"))
    written.append(str(summary))
    return tuple(written)


__all__ = [
    "DECISION_MISMATCH_REASONS",
    "MISSING_FROM_REPLAY",
    "PARITY_COMPARED",
    "PARITY_FENCE_DATABASE_MISMATCH",
    "PARITY_NOT_COMPARED",
    "PARITY_NOT_COMPARED_NOTE",
    "REPLAY_WINDOW",
    "SWEEP_LATEST_START",
    "DecisionComparer",
    "DecisionParity",
    "LiveHour",
    "NightlyEnvironment",
    "NightlyEvidence",
    "check_decision_parity",
    "collect_evidence",
    "decision_mismatches",
    "decisions_not_compared",
    "fence_database",
    "fence_replay_mismatches",
    "sweep_allowed",
    "week_start",
    "write_evidence",
]
