"""The identity size store: proposals, decisions, size events, evaluations (migration 18).

Owner decision D2 (2026-10-18): an identity's size lives in the database and
changes only at a session start. A step up needs the owner's approval from
Telegram, always; a step down applies by itself at the next session. Plan
P3-C registers the tables; the writers come later (P3-E: the nightly
``trading/research/size_writer.py``; P3-D: the Telegram ``/approve`` handler
in ``trading/operations/telegram_size_approval.py``), and a test allows no
other module to insert a size event. The shadow child reads the store once
at its session start (``trading.risk.identity_sizing``).

Tables (all ``autotrader_size_*``), every one append-only: a BEFORE
UPDATE/DELETE trigger aborts any change, and a BEFORE INSERT trigger refuses
an existing key, so ``INSERT OR REPLACE``, ``REPLACE INTO`` and upserts cannot
rewrite a row either (they delete without firing the delete trigger) and
``OR IGNORE`` cannot drop one in silence. The keyed tables have no implicit
rowid to replace through (WITHOUT ROWID). Conventions as in
``trading.storage.manager_schema``, stricter where a row must stay readable
forever: timestamps are UTC exactly as Python's ``isoformat`` writes them
(``_utc_exact``), dates are real dates from 2000 on, and an event's
``effective_session`` is a weekday (the reader refuses two events at one
session start, holidays included):

``proposals``    one per proposed step: a 10-hex id, the night it was
                 evaluated (``as_of``), the identity, ``step_up`` or
                 ``step_down``, one rung of the ladder (a step down from the
                 bottom rung goes to ``'0'``: shadow-only, fenced, never a
                 size), the evidence and its fingerprint, the nights
                 evaluated since the last step, the rule version, and for a
                 step up its approval deadline.
``decisions``    at most one per proposal (its primary key): a step up is
                 ``approved`` (only by ``owner_telegram``, before its
                 deadline), ``rejected``, ``expired`` or ``superseded``; a
                 step down is ``auto_applied``, or ``fenced``/``would_fence``
                 from the bottom rung.
``events``       the size changes themselves, PAPER only: one rung from the
                 identity's current size, at most one per proposal, each
                 effective at a later session than the identity's previous
                 one and never before the day it was written. A ``step_up``
                 needs its proposal's ``approved`` decision by the owner; a
                 ``step_down`` its ``auto_applied`` decision. The ceiling
                 (x1, the top rung) is the ``to_mult`` CHECK.
``evaluations``  the promoter's verdict per identity and night, once
                 (``UNIQUE(identity, as_of)``).

The view ``autotrader_size_proposal_state`` reads each proposal with its
decision, ``pending`` while it has none (as the switch view reads OFF while
the autopilot events are empty).

The ladder rungs are text literals here, not ``identity_sizing.SIZE_LADDER``
itself: migrations load with the bridge's import chain (``manual_orders`` ->
``db.core``), which must not pull in the risk package. A test pins them
equal. Changing the ladder therefore needs a new migration, as it should.
"""
from __future__ import annotations

# The manager tables' column checks: one implementation for both stores (their
# text is part of each migration's frozen checksum either way).
from trading.storage.manager_schema import (
    _append_only,
    _day,
    _digest,
    _json,
    _token,
)

SIZE_STORE_MIGRATION = 18
SIZE_PROPOSALS_TABLE = "autotrader_size_proposals"
SIZE_DECISIONS_TABLE = "autotrader_size_decisions"
SIZE_EVENTS_TABLE = "autotrader_size_events"
SIZE_EVALUATIONS_TABLE = "autotrader_size_evaluations"
SIZE_PROPOSAL_STATE_VIEW = "autotrader_size_proposal_state"
SIZE_STORE_TABLES = (
    SIZE_PROPOSALS_TABLE,
    SIZE_DECISIONS_TABLE,
    SIZE_EVENTS_TABLE,
    SIZE_EVALUATIONS_TABLE,
)

# trading.risk.identity_sizing.SIZE_LADDER as the text the store holds
# (str(Decimal) of each rung, bottom to top; a test pins the two equal).
SIZE_RUNGS = ("0.25", "0.5", "1")
# A step down from the bottom rung: shadow-only (a fence), never a size.
SHADOW_ONLY_MULT = "0"
# The only account mode a size event may name (owner decision D3: paper only;
# LIVE has no path and gets its own reviewed ceiling first).
PAPER_ACCOUNT_MODE = "paper"
# The only actor whose approval can raise a size (owner decision D2).
OWNER_TELEGRAM_ACTOR = "owner_telegram"
# An event takes effect at most this many days after it was written: the next
# session is at most a long weekend plus holidays away, and a date far off is
# a writer defect the session calendar may not even span (N19a-2 LOW-1).
SIZE_EVENT_HORIZON_DAYS = 14

STEP_UP = "step_up"
STEP_DOWN = "step_down"
STEP_UP_DECISIONS = ("approved", "rejected", "expired", "superseded")
STEP_DOWN_DECISIONS = ("auto_applied",)
SHADOW_ONLY_DECISIONS = ("fenced", "would_fence")

# Named refusals, the RAISE text of the store's own triggers.
SIZE_STEP_UP_NEEDS_OWNER_APPROVAL = "size_step_up_needs_owner_approval"
SIZE_STEP_DOWN_NEEDS_AUTO_APPLIED = "size_step_down_needs_auto_applied"
SIZE_STEP_NOT_FROM_CURRENT = "size_step_not_from_current"
SIZE_EVENT_NOT_AFTER_PREVIOUS = "size_event_not_after_previous"
SIZE_DECISION_NOT_FOR_PROPOSAL = "size_decision_not_for_proposal"
SIZE_APPROVAL_AFTER_DEADLINE = "size_approval_after_deadline"


def _quoted(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _one_rung(*, down_to_shadow_only: bool) -> str:
    """``kind`` with ``from_mult -> to_mult`` exactly one rung up or down the ladder."""
    ups = [(low, high) for low, high in zip(SIZE_RUNGS, SIZE_RUNGS[1:])]
    downs = [(high, low) for low, high in ups]
    if down_to_shadow_only:
        downs.append((SIZE_RUNGS[0], SHADOW_ONLY_MULT))

    def pairs(steps: list[tuple[str, str]]) -> str:
        return " OR ".join(f"(from_mult = '{origin}' AND to_mult = '{target}')" for origin, target in steps)

    return f"((kind = '{STEP_UP}' AND ({pairs(ups)})) OR (kind = '{STEP_DOWN}' AND ({pairs(downs)})))"


def _utc_exact(column: str) -> str:
    """UTC text exactly as Python's ``isoformat`` writes it, and a real instant.

    Stricter than the manager tables' ``_utc`` (N19a LOW-6): that one lets
    SQLite normalise an impossible time (``2026-02-30``, hour 24) that Python
    then cannot parse, and an append-only row that can never be read would
    block its identity for good. No fraction or exactly six digits; a year
    from 2000; hour 00-23; ``datetime()`` must give back the same instant.
    """
    stamp = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]"
    return (
        f"(({column} GLOB '{stamp}+00:00'"
        f" OR {column} GLOB '{stamp}.[0-9][0-9][0-9][0-9][0-9][0-9]+00:00')"
        f" AND substr({column}, 1, 4) >= '2000' AND substr({column}, 12, 2) <= '23'"
        f" AND datetime({column}) IS replace(substr({column}, 1, 19), 'T', ' '))"
    )


def _calendar_day(column: str) -> str:
    """A real ``YYYY-MM-DD`` date from 2000 on (Python parses every one)."""
    return f"({_day(column)} AND {column} >= '2000-01-01')"


def _weekday(column: str) -> str:
    """A calendar day from Monday to Friday: the shape of a session date.

    Holidays are the reader's: it maps each event to its first XNYS session
    and refuses two at one session start (N19a LOW-5)."""
    return f"({_calendar_day(column)} AND strftime('%w', {column}) NOT IN ('0', '6'))"


def _no_replace(table: str, noun: str, key: str) -> str:
    """Refuse an insert whose key already exists, whatever its conflict clause.

    ``INSERT OR REPLACE``, ``REPLACE INTO`` and an upsert resolve a key
    conflict by deleting the old row without firing its BEFORE DELETE trigger
    (``recursive_triggers`` is off), so ``_append_only`` alone let a rejection
    become an approval or a step down disappear (N19a MEDIUM-1). A trigger's
    RAISE(ABORT) also overrides ``OR IGNORE``: a duplicate is refused loudly,
    never dropped in silence. ``key`` compares ``existing`` with ``NEW``.
    """
    return f"""CREATE TRIGGER IF NOT EXISTS {table}_no_replace
    BEFORE INSERT ON {table}
    WHEN EXISTS (SELECT 1 FROM {table} AS existing WHERE {key})
    BEGIN SELECT RAISE(ABORT, '{noun} are append-only'); END"""


def _identity(column: str) -> str:
    """A catalogue identity name such as MOMO_HOD_V2."""
    return (
        f"(length({column}) BETWEEN 1 AND 64 AND {column} GLOB '[A-Z]*'"
        f" AND {column} NOT GLOB '*[^A-Z0-9_]*')"
    )


_RUNG = _quoted(SIZE_RUNGS)
_PROPOSAL_TARGET = _quoted((SHADOW_ONLY_MULT, *SIZE_RUNGS))
_KINDS = _quoted((STEP_UP, STEP_DOWN))
_DECISIONS = _quoted((*STEP_UP_DECISIONS, *STEP_DOWN_DECISIONS, *SHADOW_ONLY_DECISIONS))

SIZE_PROPOSALS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS {SIZE_PROPOSALS_TABLE} (
        proposal_id TEXT PRIMARY KEY
            CHECK(length(proposal_id) = 10 AND proposal_id NOT GLOB '*[^0-9a-f]*'),
        as_of TEXT NOT NULL CHECK{_calendar_day("as_of")},
        identity TEXT NOT NULL CHECK{_identity("identity")},
        kind TEXT NOT NULL CHECK(kind IN {_KINDS}),
        from_mult TEXT NOT NULL CHECK(from_mult IN {_RUNG}),
        to_mult TEXT NOT NULL CHECK(to_mult IN {_PROPOSAL_TARGET}),
        evidence_json TEXT NOT NULL CHECK{_json("evidence_json", "object")},
        evidence_fingerprint TEXT NOT NULL CHECK{_digest("evidence_fingerprint")},
        nights_since_last_step INTEGER NOT NULL CHECK(nights_since_last_step >= 0),
        deadline_at TEXT NULL CHECK(deadline_at IS NULL OR {_utc_exact("deadline_at")}),
        rule_version TEXT NOT NULL
            CHECK(length(rule_version) BETWEEN 1 AND 64 AND rule_version NOT GLOB '*[^a-z0-9.-]*'),
        created_at TEXT NOT NULL CHECK{_utc_exact("created_at")},
        CHECK{_one_rung(down_to_shadow_only=True)},
        -- A step up waits for the owner until its deadline; a step down never waits.
        CHECK((kind = '{STEP_UP}') = (deadline_at IS NOT NULL)),
        CHECK(deadline_at IS NULL OR julianday(deadline_at) > julianday(created_at))
    ) WITHOUT ROWID""",
    *_append_only(SIZE_PROPOSALS_TABLE, "size proposals"),
    _no_replace(SIZE_PROPOSALS_TABLE, "size proposals", "existing.proposal_id = NEW.proposal_id"),
)

SIZE_DECISIONS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS {SIZE_DECISIONS_TABLE} (
        proposal_id TEXT PRIMARY KEY,
        decision TEXT NOT NULL CHECK(decision IN {_DECISIONS}),
        actor TEXT NOT NULL CHECK{_token("actor")},
        at TEXT NOT NULL CHECK{_utc_exact("at")},
        -- Only the owner, from Telegram, can approve a size up (D2).
        CHECK(decision != 'approved' OR actor = '{OWNER_TELEGRAM_ACTOR}'),
        FOREIGN KEY(proposal_id) REFERENCES {SIZE_PROPOSALS_TABLE}(proposal_id)
    ) WITHOUT ROWID""",
    *_append_only(SIZE_DECISIONS_TABLE, "size decisions"),
    _no_replace(SIZE_DECISIONS_TABLE, "size decisions", "existing.proposal_id = NEW.proposal_id"),
    # The decision fits its proposal's kind (and exists: whatever the
    # foreign-key pragma of the writing connection).
    f"""CREATE TRIGGER IF NOT EXISTS {SIZE_DECISIONS_TABLE}_fit_proposal
    BEFORE INSERT ON {SIZE_DECISIONS_TABLE}
    WHEN NOT EXISTS (
        SELECT 1 FROM {SIZE_PROPOSALS_TABLE} p
        WHERE p.proposal_id = NEW.proposal_id
          AND ((p.kind = '{STEP_UP}' AND NEW.decision IN {_quoted(STEP_UP_DECISIONS)})
            OR (p.kind = '{STEP_DOWN}' AND p.to_mult != '{SHADOW_ONLY_MULT}'
                AND NEW.decision IN {_quoted(STEP_DOWN_DECISIONS)})
            OR (p.kind = '{STEP_DOWN}' AND p.to_mult = '{SHADOW_ONLY_MULT}'
                AND NEW.decision IN {_quoted(SHADOW_ONLY_DECISIONS)}))
    )
    BEGIN SELECT RAISE(ABORT, '{SIZE_DECISION_NOT_FOR_PROPOSAL}'); END""",
    # An approval counts only before the proposal's deadline (the next
    # session's start): a late one can never raise a size. (Only a step up is
    # approved, and a step up always has its deadline.)
    f"""CREATE TRIGGER IF NOT EXISTS {SIZE_DECISIONS_TABLE}_approval_in_time
    BEFORE INSERT ON {SIZE_DECISIONS_TABLE}
    WHEN NEW.decision = 'approved' AND EXISTS (
        SELECT 1 FROM {SIZE_PROPOSALS_TABLE} p
        WHERE p.proposal_id = NEW.proposal_id
          AND p.deadline_at IS NOT NULL AND julianday(NEW.at) >= julianday(p.deadline_at)
    )
    BEGIN SELECT RAISE(ABORT, '{SIZE_APPROVAL_AFTER_DEADLINE}'); END""",
)

SIZE_EVENTS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS {SIZE_EVENTS_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT CHECK(id >= 1),
        identity TEXT NOT NULL CHECK{_identity("identity")},
        from_mult TEXT NOT NULL CHECK(from_mult IN {_RUNG}),
        to_mult TEXT NOT NULL CHECK(to_mult IN {_RUNG}),
        effective_session TEXT NOT NULL CHECK{_weekday("effective_session")},
        kind TEXT NOT NULL CHECK(kind IN {_KINDS}),
        proposal_id TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL CHECK{_utc_exact("created_at")},
        account_mode TEXT NOT NULL CHECK(account_mode = '{PAPER_ACCOUNT_MODE}'),
        CHECK{_one_rung(down_to_shadow_only=False)},
        -- Never back-dated: an event takes effect on or after the day it was written,
        -- and never further ahead than the horizon (from NEW.created_at alone).
        CHECK(effective_session >= date(created_at)),
        CHECK(effective_session <= date(created_at, '+{SIZE_EVENT_HORIZON_DAYS} days')),
        FOREIGN KEY(proposal_id) REFERENCES {SIZE_PROPOSALS_TABLE}(proposal_id)
    )""",
    f"""CREATE INDEX IF NOT EXISTS idx_{SIZE_EVENTS_TABLE}_identity
        ON {SIZE_EVENTS_TABLE}(identity, account_mode, id)""",
    *_append_only(SIZE_EVENTS_TABLE, "size events"),
    _no_replace(
        SIZE_EVENTS_TABLE, "size events", "existing.id = NEW.id OR existing.proposal_id = NEW.proposal_id"
    ),
    # D2: a size goes up only on the owner's own approval of this very step.
    f"""CREATE TRIGGER IF NOT EXISTS {SIZE_EVENTS_TABLE}_step_up_approved
    BEFORE INSERT ON {SIZE_EVENTS_TABLE}
    WHEN NEW.kind = '{STEP_UP}' AND NOT EXISTS (
        SELECT 1 FROM {SIZE_PROPOSALS_TABLE} p
        JOIN {SIZE_DECISIONS_TABLE} d ON d.proposal_id = p.proposal_id
        WHERE p.proposal_id = NEW.proposal_id AND p.kind = '{STEP_UP}'
          AND p.identity = NEW.identity AND p.from_mult = NEW.from_mult AND p.to_mult = NEW.to_mult
          AND d.decision = 'approved' AND d.actor = '{OWNER_TELEGRAM_ACTOR}'
    )
    BEGIN SELECT RAISE(ABORT, '{SIZE_STEP_UP_NEEDS_OWNER_APPROVAL}'); END""",
    # D2: a step down applies by itself, but only as its proposal's recorded
    # automatic decision.
    f"""CREATE TRIGGER IF NOT EXISTS {SIZE_EVENTS_TABLE}_step_down_auto_applied
    BEFORE INSERT ON {SIZE_EVENTS_TABLE}
    WHEN NEW.kind = '{STEP_DOWN}' AND NOT EXISTS (
        SELECT 1 FROM {SIZE_PROPOSALS_TABLE} p
        JOIN {SIZE_DECISIONS_TABLE} d ON d.proposal_id = p.proposal_id
        WHERE p.proposal_id = NEW.proposal_id AND p.kind = '{STEP_DOWN}'
          AND p.identity = NEW.identity AND p.from_mult = NEW.from_mult AND p.to_mult = NEW.to_mult
          AND d.decision = 'auto_applied'
    )
    BEGIN SELECT RAISE(ABORT, '{SIZE_STEP_DOWN_NEEDS_AUTO_APPLIED}'); END""",
    # One rung from the size in force: a step starts where the identity's
    # previous event ended (the first one from the reviewed baseline, which
    # the reader checks: the baseline is code, not data).
    f"""CREATE TRIGGER IF NOT EXISTS {SIZE_EVENTS_TABLE}_from_current
    BEFORE INSERT ON {SIZE_EVENTS_TABLE}
    WHEN EXISTS (
        SELECT 1 FROM {SIZE_EVENTS_TABLE} e
        WHERE e.identity = NEW.identity AND e.account_mode = NEW.account_mode
    ) AND NEW.from_mult IS NOT (
        SELECT e.to_mult FROM {SIZE_EVENTS_TABLE} e
        WHERE e.identity = NEW.identity AND e.account_mode = NEW.account_mode
        ORDER BY e.id DESC LIMIT 1
    )
    BEGIN SELECT RAISE(ABORT, '{SIZE_STEP_NOT_FROM_CURRENT}'); END""",
    # At most one step per identity and session, in order: two events for one
    # session would be two rungs at one session start.
    f"""CREATE TRIGGER IF NOT EXISTS {SIZE_EVENTS_TABLE}_after_previous
    BEFORE INSERT ON {SIZE_EVENTS_TABLE}
    WHEN NEW.effective_session <= (
        SELECT max(e.effective_session) FROM {SIZE_EVENTS_TABLE} e
        WHERE e.identity = NEW.identity AND e.account_mode = NEW.account_mode
    )
    BEGIN SELECT RAISE(ABORT, '{SIZE_EVENT_NOT_AFTER_PREVIOUS}'); END""",
)

SIZE_EVALUATIONS_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS {SIZE_EVALUATIONS_TABLE} (
        id INTEGER PRIMARY KEY CHECK(id >= 1),
        identity TEXT NOT NULL CHECK{_identity("identity")},
        as_of TEXT NOT NULL CHECK{_calendar_day("as_of")},
        status TEXT NOT NULL CHECK{_token("status")},
        fingerprint TEXT NOT NULL CHECK{_digest("fingerprint")},
        created_at TEXT NOT NULL CHECK{_utc_exact("created_at")},
        UNIQUE(identity, as_of)
    )""",
    *_append_only(SIZE_EVALUATIONS_TABLE, "size evaluations"),
    _no_replace(
        SIZE_EVALUATIONS_TABLE,
        "size evaluations",
        "existing.id = NEW.id OR (existing.identity = NEW.identity AND existing.as_of = NEW.as_of)",
    ),
)

SIZE_PROPOSAL_STATE_SCHEMA = (
    f"""CREATE VIEW IF NOT EXISTS {SIZE_PROPOSAL_STATE_VIEW} AS
    SELECT p.proposal_id, p.as_of, p.identity, p.kind, p.from_mult, p.to_mult, p.deadline_at,
           COALESCE(d.decision, 'pending') AS state, d.actor AS decided_by, d.at AS decided_at
    FROM {SIZE_PROPOSALS_TABLE} p
    LEFT JOIN {SIZE_DECISIONS_TABLE} d ON d.proposal_id = p.proposal_id""",
)

# Registered: migration 18 (P3-C). Frozen once applied. Proposals before
# decisions before events (their triggers read both).
SIZE_STORE_SCHEMA = (
    *SIZE_PROPOSALS_SCHEMA,
    *SIZE_DECISIONS_SCHEMA,
    *SIZE_EVENTS_SCHEMA,
    *SIZE_EVALUATIONS_SCHEMA,
    *SIZE_PROPOSAL_STATE_SCHEMA,
)


__all__ = [
    "OWNER_TELEGRAM_ACTOR",
    "PAPER_ACCOUNT_MODE",
    "SHADOW_ONLY_DECISIONS",
    "SHADOW_ONLY_MULT",
    "SIZE_APPROVAL_AFTER_DEADLINE",
    "SIZE_DECISIONS_TABLE",
    "SIZE_DECISION_NOT_FOR_PROPOSAL",
    "SIZE_EVALUATIONS_TABLE",
    "SIZE_EVENTS_TABLE",
    "SIZE_EVENT_HORIZON_DAYS",
    "SIZE_EVENT_NOT_AFTER_PREVIOUS",
    "SIZE_PROPOSALS_TABLE",
    "SIZE_PROPOSAL_STATE_VIEW",
    "SIZE_RUNGS",
    "SIZE_STEP_DOWN_NEEDS_AUTO_APPLIED",
    "SIZE_STEP_NOT_FROM_CURRENT",
    "SIZE_STEP_UP_NEEDS_OWNER_APPROVAL",
    "SIZE_STORE_MIGRATION",
    "SIZE_STORE_SCHEMA",
    "SIZE_STORE_TABLES",
    "STEP_DOWN",
    "STEP_DOWN_DECISIONS",
    "STEP_UP",
    "STEP_UP_DECISIONS",
]
