"""Each decision's effective size multiplier, and each identity's size steps (T-8).

A size step (an owner-approved change of an identity's row in
``trading.risk.identity_sizing``) never restarts an identity's evidence: the
evidence identity (``paper_promotion.code_identity``) holds the rules, the
entry window and the risk policy, not the multiplier, and every statistic the
promotion rule reads is in R, which the quantity cancels out of. What a step
changes is recorded here instead, as an event in the nightly evidence.

Where the multiplier comes from, per decision (``effective_multiplier_for``):

``recorded``
    the decision-sizing sidecar the pipeline writes beside the database
    (``trading.storage.decision_sizing``), from T-8 on;
``derived_from_source_sha``
    for a decision recorded before the sidecar existed: its run's
    ``source_sha`` looked up in the committed table of commits
    (``trading.research.source_commits``), never in git at runtime;
``unknown``
    a commit the table does not list (or a run without a usable sha). It is
    named in the evidence and never guessed.

What production applied before T-8 is not what the constants said. Before
v7b only MOMO_HOD_V2 sent orders, at x1, and every other name was sized at the
unscaled policy quantity (x1). v7b (784c2bd6a732) added ORB_V2 x0.25 and
VWAP_PB_V2 / GAP_GO_V2 x0.5, but the repository runner rebuilt the pipeline
without its identity factor (``trading/orchestration/persistence.py`` before
T-8), so every decision those commits made through the session runner was sized
at x1. The table below records both: ``table`` (the constant) and ``applied``
(what the decisions carry). The fix ships with the sidecar, so every commit
that applies the table also records it.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
from urllib.parse import quote

from trading.research.source_commits import PRE_V7B_COMMITS, V7B_COMMITS
from trading.storage.decision_sizing import DecisionSizing, SizingIndex
from trading.storage.migrations import SCHEMA_READ_TIMEOUT_SECONDS

RECORDED = "recorded"
DERIVED = "derived_from_source_sha"
UNKNOWN = "unknown"

BECAME_SENDABLE = "became_sendable"
SIZE_STEP = "size_step"
LEFT_SENDABLE = "left_sendable"

V7B_FIRST_COMMIT = "784c2bd6a7322a8abc7afe9d8561ae904d181225"
_ONE = Decimal("1")
_HEX = re.compile(r"^[0-9a-f]{12,64}$")
_DIRTY = "-dirty"
_SQL_BATCH = 400


def _frozen(table: Mapping[str, str]) -> Mapping[str, Decimal]:
    return MappingProxyType({name: Decimal(value) for name, value in table.items()})


@dataclass(frozen=True, slots=True)
class TableVersion:
    """One era of identity sizing, as the code at those commits ran it."""

    label: str
    first_commit: str | None
    sendable: frozenset[str]
    # IDENTITY_SIZE_MULTIPLIER as committed (empty before v7a added the module).
    table: Mapping[str, Decimal]
    # The factor each sendable identity's decisions were actually sized with.
    applied: Mapping[str, Decimal]
    note: str

    def factor(self, name: str) -> tuple[Decimal, bool]:
        """(identity factor, sendable) for a catalogue name; shadow names are x1."""
        if name not in self.sendable:
            return _ONE, False
        return self.applied[name], True


PRE_V7B = TableVersion(
    label="pre-v7b",
    first_commit=None,
    sendable=frozenset({"MOMO_HOD_V2"}),
    table=_frozen({"MOMO_HOD_V2": "1"}),
    applied=_frozen({"MOMO_HOD_V2": "1"}),
    note="MOMO_HOD_V2 sends at x1; ORB_V2, VWAP_PB_V2 and GAP_GO_V2 are shadow-only and unscaled",
)
V7B_FACTOR_DROPPED = TableVersion(
    label="v7b-factor-dropped",
    first_commit=V7B_FIRST_COMMIT,
    sendable=frozenset({"MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2"}),
    table=_frozen({"MOMO_HOD_V2": "1", "ORB_V2": "0.25", "VWAP_PB_V2": "0.5", "GAP_GO_V2": "0.5"}),
    applied=_frozen({"MOMO_HOD_V2": "1", "ORB_V2": "1", "VWAP_PB_V2": "1", "GAP_GO_V2": "1"}),
    note=(
        "the repository runner rebuilt the pipeline without its identity factor:"
        " every decision was sized x1 whatever the table said"
    ),
)
TABLE_VERSIONS: tuple[TableVersion, ...] = (PRE_V7B, V7B_FACTOR_DROPPED)


@dataclass(frozen=True, slots=True)
class SourceLookup:
    version: TableVersion | None
    commit: str | None
    dirty: bool = False


def table_version_for(source_sha: str) -> SourceLookup:
    """The table version a run's ``source_sha`` ran with; ``None`` when not listed.

    A ``-dirty`` run (uncommitted changes in the checkout) is read as its
    commit and marked: the sizing module is code, and a dirty tree in the
    live checkout has so far only meant local operator files.
    """
    text = str(source_sha or "").strip().lower()
    dirty = text.endswith(_DIRTY)
    if dirty:
        text = text[: -len(_DIRTY)]
    if not _HEX.fullmatch(text):
        return SourceLookup(version=None, commit=None, dirty=dirty)
    prefix = text[:12]
    if prefix in V7B_COMMITS:
        return SourceLookup(V7B_FACTOR_DROPPED, prefix, dirty)
    if prefix in PRE_V7B_COMMITS:
        return SourceLookup(PRE_V7B, prefix, dirty)
    return SourceLookup(version=None, commit=prefix, dirty=dirty)


# --- one decision --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecisionRef:
    """What research knows about one recorded decision."""

    decision_id: str
    run_id: str
    session_date: date
    strategy_name: str
    source_sha: str
    # The AI review's decision ("PROPOSE", "ABSTAIN") or None without a review.
    review: str | None = None
    # day_step x setup_step of the decision's managed row, when it has one.
    managed_steps: Decimal | None = None


@dataclass(frozen=True, slots=True)
class EffectiveMultiplier:
    value: Decimal | None
    identity_factor: Decimal | None
    managed_factor: Decimal | None
    sendable: bool | None
    source: str
    table_version: str | None = None
    note: str = ""

    def payload(self) -> dict[str, object]:
        def text(value: Decimal | None) -> str | None:
            return None if value is None else _plain(value)

        return {
            "size_multiplier_effective": text(self.value),
            "identity_factor": text(self.identity_factor),
            "managed_factor": text(self.managed_factor),
            "sendable": self.sendable,
            "source": self.source,
            "table_version": self.table_version,
            "note": self.note,
        }


def _plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if Decimal(text) == 0 else text


def effective_multiplier_for(
    decision: DecisionRef, *, recorded: Mapping[str, DecisionSizing] | None = None
) -> EffectiveMultiplier:
    """identity factor x managed factor for ``decision``: recorded, else derived, else unknown.

    Derived: the identity factor its run's commit applied (``TABLE_VERSIONS``)
    times the manager's steps when the review proposed and a managed row
    exists (the pipeline's managed factor), else 1.
    """
    row = (recorded or {}).get(decision.decision_id)
    if row is not None:
        return EffectiveMultiplier(
            value=row.size_multiplier_effective,
            identity_factor=row.identity_factor,
            managed_factor=row.managed_factor,
            sendable=row.sendable,
            source=RECORDED,
            note="" if row.identity_factor is not None else "identity_sizing_missing",
        )
    lookup = table_version_for(decision.source_sha)
    if lookup.version is None:
        return EffectiveMultiplier(
            value=None,
            identity_factor=None,
            managed_factor=None,
            sendable=None,
            source=UNKNOWN,
            note=f"source {lookup.commit or decision.source_sha!r} is not in the commit table",
        )
    identity, sendable = lookup.version.factor(decision.strategy_name)
    managed = (
        decision.managed_steps
        if decision.review == "PROPOSE" and decision.managed_steps is not None
        else _ONE
    )
    return EffectiveMultiplier(
        value=identity * managed,
        identity_factor=identity,
        managed_factor=managed,
        sendable=sendable,
        source=DERIVED,
        table_version=lookup.version.label,
        note="dirty checkout" if lookup.dirty else "",
    )


# --- one identity's size steps --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunRef:
    run_id: str
    session_date: date
    strategy_name: str
    source_sha: str


@dataclass(frozen=True, slots=True)
class RunFactor:
    factor: Decimal | None
    sendable: bool | None
    source: str
    note: str = ""


def run_factor(run: RunRef, records: Sequence[DecisionSizing] = ()) -> RunFactor:
    """The identity factor a run sized with: its recorded decisions, else its commit."""
    if records:
        known = {(row.identity_factor, row.sendable) for row in records}
        if len(known) != 1:
            return RunFactor(None, None, UNKNOWN, "conflicting records in one run")
        ((factor, sendable),) = known
        if factor is None:
            return RunFactor(None, sendable, UNKNOWN, "identity_sizing_missing")
        return RunFactor(factor, sendable, RECORDED)
    lookup = table_version_for(run.source_sha)
    if lookup.version is None:
        return RunFactor(None, None, UNKNOWN, lookup.commit or str(run.source_sha))
    factor, sendable = lookup.version.factor(run.strategy_name)
    return RunFactor(factor, sendable, f"{DERIVED}:{lookup.version.label}")


@dataclass(frozen=True, slots=True)
class SizeSession:
    session_date: date
    factor: Decimal | None
    sendable: bool | None
    source: str

    @property
    def known(self) -> bool:
        return self.factor is not None and self.sendable is not None


@dataclass(frozen=True, slots=True)
class SizeEvent:
    session_date: date
    kind: str
    before: Decimal | None
    after: Decimal | None
    source: str

    def payload(self) -> dict[str, object]:
        return {
            "session_date": self.session_date.isoformat(),
            "event": self.kind,
            "from": None if self.before is None else _plain(self.before),
            "to": None if self.after is None else _plain(self.after),
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class IdentitySizeHistory:
    """One identity's sessions by size, and every change of size between them."""

    identity: str
    sessions: tuple[SizeSession, ...] = ()
    events: tuple[SizeEvent, ...] = ()
    unknown_sources: tuple[str, ...] = ()

    @property
    def unknown_sessions(self) -> tuple[date, ...]:
        return tuple(item.session_date for item in self.sessions if not item.known)

    def latest_factor(self) -> Decimal | None:
        """The size of the newest session it sent at (``None`` when it never did)."""
        for item in reversed(self.sessions):
            if item.known and item.sendable:
                return item.factor
        return None

    def sessions_since_last_step(self, current: Decimal) -> int:
        """Sendable sessions at ``current`` since the last step (becoming sendable is one).

        An unknown session ends the count, because it could hide a step; a
        current size no session has run with yet is 0 sessions.
        """
        count = 0
        for item in reversed(self.sessions):
            if not item.known:
                break
            if not item.sendable:
                break
            if item.factor != current:
                break
            count += 1
        return count

    def payload(self, current: Decimal | None) -> dict[str, object]:
        latest = self.latest_factor()
        return {
            "identity": self.identity,
            "current": None if current is None else _plain(current),
            "latest_observed": None if latest is None else _plain(latest),
            "sessions_since_last_step": None if current is None else self.sessions_since_last_step(current),
            "events": [event.payload() for event in self.events],
            "unknown_sessions": [day.isoformat() for day in self.unknown_sessions],
            "unknown_sources": list(self.unknown_sources),
        }


def identity_size_history(identity: str, runs: Sequence[RunRef], index: SizingIndex) -> IdentitySizeHistory:
    """One session per date: the factor its runs sized with, or unknown when they disagree."""
    by_run = index.by_run()
    per_day: dict[date, list[RunFactor]] = defaultdict(list)
    unknown_sources: list[str] = []
    for run in runs:
        if run.strategy_name != identity:
            continue
        result = run_factor(run, by_run.get(run.run_id, ()))
        per_day[run.session_date].append(result)
        if result.source == UNKNOWN and result.note and result.note not in unknown_sources:
            unknown_sources.append(result.note)
    sessions: list[SizeSession] = []
    for day in sorted(per_day):
        results = per_day[day]
        values = {(item.factor, item.sendable) for item in results}
        if len(values) != 1 or any(item.source == UNKNOWN for item in results):
            sessions.append(SizeSession(day, None, None, UNKNOWN))
            continue
        ((factor, sendable),) = values
        sources = sorted({item.source for item in results})
        sessions.append(SizeSession(day, factor, sendable, "+".join(sources)))
    events: list[SizeEvent] = []
    previous: SizeSession | None = None
    for item in sessions:
        if not item.known:
            continue
        if item.sendable and (previous is None or not previous.sendable):
            events.append(SizeEvent(item.session_date, BECAME_SENDABLE, None, item.factor, item.source))
        elif item.sendable and previous is not None and item.factor != previous.factor:
            events.append(SizeEvent(item.session_date, SIZE_STEP, previous.factor, item.factor, item.source))
        elif not item.sendable and previous is not None and previous.sendable:
            events.append(SizeEvent(item.session_date, LEFT_SENDABLE, previous.factor, None, item.source))
        previous = item
    return IdentitySizeHistory(
        identity=identity,
        sessions=tuple(sessions),
        events=tuple(events),
        unknown_sources=tuple(unknown_sources),
    )


# --- reading the ledger (read-only) ---------------------------------------------------

_RUNS = """
    SELECT r.id, r.session_date, r.source_sha, json_extract(v.spec_json, '$.name')
    FROM autotrader_runs r
    JOIN autotrader_strategy_versions v
      ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
    WHERE r.mode = 'shadow'
    ORDER BY r.session_date, r.id
"""
_DECISIONS = """
    SELECT d.id, d.run_id, r.session_date, r.source_sha, json_extract(v.spec_json, '$.name'),
           json_extract(d.payload_json, '$.ai_review.decision')
    FROM autotrader_candidate_decisions d
    JOIN autotrader_runs r ON r.id = d.run_id
    JOIN autotrader_strategy_versions v
      ON v.strategy_id = d.strategy_id AND v.version = d.strategy_version
    WHERE d.id IN ({marks})
"""
_MANAGED = """
    SELECT decision_id, day_step, setup_step FROM autotrader_manager_decisions
    WHERE decision_id IN ({marks})
"""


def _connect(database: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(database.expanduser().resolve()))}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)


def _tables(db: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def read_run_refs(database: str | Path, *, names: Collection[str]) -> tuple[RunRef, ...]:
    """Every shadow run of the named identities; a missing database reads as none."""
    path = Path(database).expanduser()
    if not path.is_file():
        return ()
    wanted = set(names)
    refs: list[RunRef] = []
    with closing(_connect(path)) as db:
        for run_id, session_date, source_sha, name in db.execute(_RUNS):
            if str(name or "").strip() not in wanted:
                continue
            try:
                day = date.fromisoformat(str(session_date))
            except ValueError:
                continue
            refs.append(RunRef(str(run_id), day, str(name).strip(), str(source_sha)))
    return tuple(refs)


def read_decision_refs(database: str | Path, *, decision_ids: Collection[str]) -> dict[str, DecisionRef]:
    """The named decisions with their run's commit, review and managed steps (read-only)."""
    path = Path(database).expanduser()
    wanted = sorted({str(item) for item in decision_ids})
    if not wanted or not path.is_file():
        return {}
    refs: dict[str, DecisionRef] = {}
    steps: dict[str, Decimal] = {}
    with closing(_connect(path)) as db:
        managed = "autotrader_manager_decisions" in _tables(db)
        for start in range(0, len(wanted), _SQL_BATCH):
            batch = wanted[start : start + _SQL_BATCH]
            marks = ",".join("?" for _ in batch)
            if managed:
                for decision_id, day_step, setup_step in db.execute(_MANAGED.format(marks=marks), batch):
                    try:
                        steps[str(decision_id)] = Decimal(str(day_step)) * Decimal(str(setup_step))
                    except InvalidOperation:
                        continue
            for decision_id, run_id, session_date, source_sha, name, review in db.execute(
                _DECISIONS.format(marks=marks), batch
            ):
                try:
                    day = date.fromisoformat(str(session_date))
                except ValueError:
                    continue
                refs[str(decision_id)] = DecisionRef(
                    decision_id=str(decision_id),
                    run_id=str(run_id),
                    session_date=day,
                    strategy_name=str(name or "").strip(),
                    source_sha=str(source_sha),
                    review=None if review is None else str(review),
                    managed_steps=steps.get(str(decision_id)),
                )
    return refs


@dataclass(frozen=True, slots=True)
class SizeSteps:
    """The ``size_steps`` section of evidence.json: per identity, its sizes and steps."""

    histories: Mapping[str, IdentitySizeHistory] = field(default_factory=dict)
    current: Mapping[str, Decimal] = field(default_factory=dict)
    sidecar_unreadable: tuple[str, ...] = ()
    readable: bool = True
    note: str = ""

    def payload(self) -> dict[str, object]:
        return {
            "basis": (
                "identity factor per session: the decision-sizing sidecar, else the run's"
                " source_sha in the committed commit table; a size step never restarts evidence"
            ),
            "readable": self.readable,
            "note": self.note,
            "sidecar_unreadable": list(self.sidecar_unreadable),
            "identities": {
                name: history.payload(self.current.get(name)) for name, history in self.histories.items()
            },
        }


def read_size_steps(
    database: str | Path,
    index: SizingIndex,
    *,
    current: Mapping[str, Decimal],
) -> SizeSteps:
    """Every identity in ``current`` (its size now), read-only; failures are named, never raised."""
    try:
        runs = read_run_refs(database, names=current)
    except sqlite3.Error as exc:
        return SizeSteps(current=dict(current), readable=False, note=type(exc).__name__)
    return SizeSteps(
        histories={name: identity_size_history(name, runs, index) for name in current},
        current=dict(current),
        sidecar_unreadable=index.unreadable,
    )


__all__ = [
    "BECAME_SENDABLE",
    "DERIVED",
    "LEFT_SENDABLE",
    "PRE_V7B",
    "RECORDED",
    "SIZE_STEP",
    "TABLE_VERSIONS",
    "UNKNOWN",
    "V7B_FACTOR_DROPPED",
    "V7B_FIRST_COMMIT",
    "DecisionRef",
    "EffectiveMultiplier",
    "IdentitySizeHistory",
    "RunFactor",
    "RunRef",
    "SizeEvent",
    "SizeSession",
    "SizeSteps",
    "SourceLookup",
    "TableVersion",
    "effective_multiplier_for",
    "identity_size_history",
    "read_decision_refs",
    "read_run_refs",
    "read_size_steps",
    "run_factor",
    "table_version_for",
]
