"""Read-only check of one PAPER bridge session (W6.4, first v7b session 2026-10-19).

``autotraderctl session check --date D`` reads, for the New York session D,
only these inputs and never changes any of them:

* the ``bridge.log`` section of D: every bridge process the supervisor
  launched with ``--date D`` (its ``=== <ISO> supervisor starts bridge: ...``
  banner), from the ``bridge.log.YYYY-MM-DD`` archives and ``bridge.log``;
  a process without a banner counts when its own lines name D;
* ``supervisor.log`` and its archives, less the lines a rotation carried
  (``log_tailer.without_carried``): the bridge child's starts and exits on D;
* the bridge's own state ``<data>/bridge/D.json``;
* the database, opened ``file:...?mode=ro`` with ``query_only`` (never
  written, never migrated): the decisions exactly as the bridge reads them
  (``DECISIONS_SQL``, ``parse_intent``), the account snapshots a decision may
  have been sized from, and ``autotrader_execution_fences``.

Six rules, each PASS, WARN or FAIL with its evidence lines:

1. identities: every SENT names a sendable identity of that day's bridge and
   of ``SENDABLE_IDENTITIES``, its state bracket and its ledger decision carry
   the same name, and a shadow-only name appears only as a SKIP;
2. attribution and close-out: every FILL, EXIT and CLOSED line belongs to the
   SENT of its symbol (FILL lines name the identity; the state confirms the
   rest), nothing is "still open after flatten", a FLAT line comes at or after
   the flatten (close - 15 min), the last bridge process stopped with exit 0
   and the supervisor's last bridge exit was code 0;
3. caps: per identity, open brackets (SENT until CLOSED or CANCELLED) never
   exceed its position cap, entries (SENT attempts) never exceed its daily
   cap, and no entry comes at or after its last-entry time (the real XNYS
   close less its offset, half days included);
4. size: every SENT's shares equal its decision's intent quantity (and the
   state's), and that quantity is floor(policy quantity x identity row). The
   policy quantity is recomputed with ``trading.risk.engine.policy_quantity``
   at multiplier 1 only from recorded inputs (an account snapshot of the run
   whose portfolio fingerprint is the decision's, and the default risk policy
   by fingerprint); without them the rule says ``size check: inputs not
   recorded`` (WARN, never PASS). Either way it FAILs a quantity above the
   identity row's size even at the logged net liquidation +10 %, or above the
   bridge's order ceilings (25 % notional, 1 % risk of net liquidation);
5. daily loss: realized P&L >= -2 % of net liquidation, or no entry was sent
   once the realized loss had reached it;
6. fences: every fence row is listed; an identity (or the global switch)
   stopped during the day is a WARN, an entry sent while its fence was
   stopped is a FAIL.

Exit 0 when no rule FAILs (WARN allowed), 1 when one does, 2 when an input is
missing. Every line of the report is masked: an account id (``DU`` and five
or more letters or digits) is printed as ``DU1234567``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
from typing import Any, cast

from trading.contracts import AccountSnapshot, PortfolioSnapshot, RiskPolicy, RuleSetup
from trading.operations.log_tailer import line_time, without_carried
from trading.research.live_ledger import BRIDGE_STATE_DIRECTORY, STATE_SCHEMA_PREFIX
from trading.risk.engine import policy_quantity, scaled_quantity
from trading.risk.identity_sizing import IDENTITY_SIZE_MULTIPLIER
from trading.risk.portfolio import portfolio_from_account
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage.runtime_fence import GLOBAL_FENCE, IDENTITY_FENCE_PREFIX

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_INPUTS_MISSING = 2

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

ACCOUNT_ID = re.compile(r"DU[A-Z0-9]{5,}")
MASKED_ACCOUNT_ID = "DU1234567"

NY = MARKET_TIMEZONE


@dataclass(frozen=True, slots=True)
class IdentityCaps:
    """One sendable identity's entry limits, as the pinned bridge's ``Caps`` row."""

    max_positions: int
    max_entries_per_day: int | None
    last_entry_before_close: timedelta


# The pinned bridge's IDENTITY_CAPS (scripts_local/paper_bridge.py v7b), row for
# row: the checker never imports the bridge, so a test loads it and pins the two
# equal (and the names equal to SENDABLE_IDENTITIES).
BRIDGE_IDENTITY_CAPS: Mapping[str, IdentityCaps] = MappingProxyType({
    "MOMO_HOD_V2": IdentityCaps(3, None, timedelta(minutes=60)),
    "ORB_V2": IdentityCaps(1, 1, timedelta(minutes=30)),
    "VWAP_PB_V2": IdentityCaps(1, 2, timedelta(minutes=30)),
    "GAP_GO_V2": IdentityCaps(1, 1, timedelta(minutes=30)),
})
# The bridge's session checkpoints and order ceilings (paper_bridge.py
# LAST_ENTRY_BEFORE_CLOSE, FLATTEN_BEFORE_CLOSE, MAX_*_PCT; pinned by a test).
SESSION_LAST_ENTRY_BEFORE_CLOSE = timedelta(minutes=30)
FLATTEN_BEFORE_CLOSE = timedelta(minutes=15)
MAX_DAILY_LOSS_PCT = Decimal("2")
MAX_ORDER_NOTIONAL_PCT = Decimal("25")
MAX_ORDER_RISK_PCT = Decimal("1")
SHADOW_ONLY_REASON = "shadow-only strategy"  # paper_bridge.py SHADOW_ONLY
# A decision is sized from the strategy's own account snapshot, not the
# bridge's: without a recorded snapshot the identity-row bound allows the
# logged net liquidation to have been this much higher (a x1 ORB is x4).
NET_LIQUIDATION_BAND = Decimal("0.10")
# paper_bridge.py DECISIONS_SQL verbatim (a test pins the two equal): the
# checker reads the decisions exactly as the bridge does.
DECISIONS_SQL = """
    SELECT d.id, d.run_id, c.symbol, c.con_id, c.contract_json, d.payload_json,
           d.created_at, v.spec_json
    FROM autotrader_candidate_decisions d
    JOIN autotrader_runs r ON r.id = d.run_id
    JOIN autotrader_candidates c ON c.id = d.candidate_id
    LEFT JOIN autotrader_strategy_versions v
      ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
    WHERE d.outcome = 'shadow_accepted' AND r.session_date = ?
    ORDER BY d.created_at, d.id
"""
ACCOUNT_SNAPSHOTS_SQL = (
    "SELECT payload_json FROM autotrader_account_snapshots WHERE run_id = ? ORDER BY observed_at, id"
)
FENCES_SQL = "SELECT name, stopped, reason, updated_at FROM autotrader_execution_fences ORDER BY name"

BRIDGE_LOG = "bridge.log"
SUPERVISOR_LOG = "supervisor.log"
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_STATE_BYTES = 5 * 1024 * 1024
# Phrases of the bridge's not-flat alerts (not_flat_message, report_state_after_close).
NOT_FLAT_PHRASES = ("still open after flatten", "still open per bridge state", "unreconciled bracket",
                    "exceed entry fills", "may be short")

_BANNER = re.compile(r"^=== (?P<at>\S+) supervisor starts bridge: (?P<argv>.*)$")
_DATE_ARGUMENT = re.compile(r"(?:^|\s)--date\s+(?P<day>\d{4}-\d{2}-\d{2})(?=\s|$)")
_EVENT = re.compile(r"^(?P<clock>\d{2}:\d{2}:\d{2}) ET\s+(?P<kind>[A-Z]+) (?P<text>.*)$")
_RUN_DAY = (
    re.compile(r"Managing brackets for (?P<day>\d{4}-\d{2}-\d{2}) until"),
    re.compile(r"/(?P<day>\d{4}-\d{2}-\d{2})\.json: \d+ brackets"),
    re.compile(r"\bfor (?P<day>\d{4}-\d{2}-\d{2}) \S+ nothing to manage"),
    re.compile(r"\bsession (?P<day>\d{4}-\d{2}-\d{2}) is over"),
)
_SHA = re.compile(r"^bridge sha256=(?P<sha>[0-9a-f]{64})")
_CONNECTED = re.compile(r"^PAPER bridge (?P<version>\S+) connected\b")
_STOPPED = re.compile(r"^bridge stopped \(exit (?P<code>-?\d+)\)")
_SENDABLE = re.compile(r"^sendable identities = (?P<names>.*)$")
_CAPS = re.compile(r"^caps (?P<name>\S+) positions=(?P<positions>\d+) entries/day=(?P<entries>\S+)"
                   r" last_entry=close-(?P<minutes>\d+)m$")
_FENCED = re.compile(r"^fenced identities = (?P<names>.*)$")
_NET_LIQ = re.compile(r"^net liquidation (?P<value>\d+(?:\.\d+)?); daily loss stop")
_SESSION_END = re.compile(r"^session end: realized P&L (?P<value>-?\d+(?:\.\d+)?)")
_SENT = re.compile(r"^(?P<symbol>.+?) \((?P<label>[^()]*)\): BUY (?P<qty>\d+) @ (?P<entry>\d+(?:\.\d+)?)"
                   r" \| stop (?P<stop>\d+(?:\.\d+)?) \| target (?P<target>\d+(?:\.\d+)?)"
                   r" \(orders (?P<parent>\d+)-(?P<last>\d+)\)$")
_FILL = re.compile(r"^(?P<symbol>.+?): (?P<filled>\d+)/(?P<qty>\d+) sh @ (?P<avg>\d+(?:\.\d+)?)"
                   r" \((?P<label>[^()]*)\)$")
_EXIT = re.compile(r"^(?P<symbol>.+?): (?P<qty>\d+) sh @ (?P<avg>\d+(?:\.\d+)?) via (?P<reason>\S+)"
                   r" \| P&L (?P<pnl>-?\d+(?:\.\d+)?)$")
_CLOSED = re.compile(r"^(?P<symbol>.+?): P&L (?P<pnl>-?\d+(?:\.\d+)?) \((?P<reason>[^()]*)\)$")
_CANCELLED = re.compile(r"^(?P<symbol>.+?): entry never filled$")
_SKIP = re.compile(r"^(?P<symbol>.+?)(?: \((?P<label>[^()]*)\))?: (?P<reason>.*)$")
_NOT_FOUND = re.compile(r"^(?P<symbol>.+?): bracket not found at IBKR")
_SUPERVISOR = re.compile(r"^(?P<at>\S+)\s+(?:!!!\s+)?(?P<event>[A-Z_]+)\s+bridge(?:\s+(?P<rest>.*))?$")
_CODE = re.compile(r"\bcode=(?P<code>\S+)")
_RESTART = re.compile(r"\brestart_in=(?P<restart>\S+)")
_ARCHIVE_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def mask_accounts(text: str) -> str:
    """Every account id (``DU`` + 5 or more letters or digits) as ``DU1234567``."""
    return ACCOUNT_ID.sub(MASKED_ACCOUNT_ID, text)


class InputsMissing(Exception):
    """An input the check needs is absent or unreadable: exit 2, no verdict."""


# --- reading: files ----------------------------------------------------------------


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _read_bytes(path: Path, limit: int) -> bytes:
    """A regular file's bytes, opened for reading only, never through a symlink."""
    if not _regular_file(path):
        raise InputsMissing(f"{path.name} is not a regular file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise InputsMissing(f"{path.name} is larger than {limit} bytes")
    return raw


def log_files(directory: Path, name: str) -> list[Path]:
    """``<name>.YYYY-MM-DD`` archives oldest first, then ``<name>`` itself (regular files only)."""
    if not directory.is_dir():
        return []
    archives = sorted(
        path for path in directory.iterdir()
        if path.name.startswith(f"{name}.") and _ARCHIVE_DAY.match(path.name[len(name) + 1:])
        and _regular_file(path)
    )
    current = directory / name
    return [*archives, *([current] if _regular_file(current) else [])]


def _lines(path: Path) -> list[str]:
    return _read_bytes(path, MAX_LOG_BYTES).decode("utf-8", errors="replace").splitlines()


# --- reading: the bridge.log section ------------------------------------------------


@dataclass(slots=True)
class LogLine:
    source: str
    number: int
    run: int
    clock: time | None
    kind: str  # the event KIND; "" for a line that is not an event (a traceback)
    text: str

    @property
    def ref(self) -> str:
        return f"{self.source}:{self.number}"

    @property
    def stamp(self) -> str:
        return f"{self.clock:%H:%M:%S} ET" if self.clock is not None else "--:--:-- ET"

    def at(self, day: date) -> datetime | None:
        return None if self.clock is None else datetime.combine(day, self.clock, tzinfo=NY)

    def evidence(self) -> str:
        return f"{self.ref} {self.stamp} {self.kind} {self.text}".rstrip()


@dataclass(slots=True)
class BridgeRun:
    """One bridge process of the session: its banner (when supervised) and its lines."""

    index: int
    source: str
    first_line: int
    banner_at: datetime | None
    lines: list[LogLine] = field(default_factory=list)
    sha256: str = ""
    version: str = ""
    connected: bool = False
    stopped: LogLine | None = None
    stopped_code: int | None = None
    sendable: tuple[str, ...] | None = None
    caps: dict[str, IdentityCaps] = field(default_factory=dict)
    fenced: list[LogLine] = field(default_factory=list)
    net_liquidation: list[Decimal] = field(default_factory=list)
    session_end_pnl: Decimal | None = None


@dataclass(slots=True)
class BridgeSection:
    session_date: date
    runs: list[BridgeRun]
    files: list[str]

    def lines(self) -> Iterator[LogLine]:
        for run in self.runs:
            yield from run.lines


@dataclass(slots=True)
class _Block:
    day: date | None
    banner_at: datetime | None
    supervised: bool
    first_line: int
    raw: list[tuple[int, str]] = field(default_factory=list)
    started: bool = False


def _iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _block_day(block: _Block) -> date | None:
    for _, raw in block.raw:
        for pattern in _RUN_DAY:
            found = pattern.search(raw)
            if found is not None:
                try:
                    return date.fromisoformat(found.group("day"))
                except ValueError:
                    return None
    return None


def _blocks(lines: Sequence[str]) -> list[_Block]:
    """One block per bridge process: a supervisor banner, else a new ``bridge sha256=`` line."""
    blocks: list[_Block] = []
    current: _Block | None = None
    for number, raw in enumerate(lines, 1):
        banner = _BANNER.match(raw)
        if banner is not None:
            argument = _DATE_ARGUMENT.search(banner.group("argv"))
            try:
                day = date.fromisoformat(argument.group("day")) if argument else None
            except ValueError:
                day = None
            current = _Block(day=day, banner_at=_iso(banner.group("at")), supervised=True, first_line=number)
            blocks.append(current)
            continue
        event = _EVENT.match(raw)
        starts = event is not None and event.group("kind") == "INFO" and bool(_SHA.match(event.group("text")))
        if current is None or (starts and (current.started or not current.supervised)):
            current = _Block(day=None, banner_at=None, supervised=False, first_line=number)
            blocks.append(current)
        current.started = current.started or starts
        current.raw.append((number, raw))
    for block in blocks:
        if not block.supervised:
            block.day = _block_day(block)
    return blocks


def _decimal(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _summarize(run: BridgeRun) -> None:
    for line in run.lines:
        text = line.text
        if line.kind == "INFO":
            if (found := _SHA.match(text)) is not None:
                run.sha256 = found.group("sha")
            elif (found := _CONNECTED.match(text)) is not None:
                run.version, run.connected = found.group("version"), True
            elif (found := _STOPPED.match(text)) is not None:
                run.stopped, run.stopped_code = line, int(found.group("code"))
            elif (found := _SENDABLE.match(text)) is not None:
                names = found.group("names").strip()
                run.sendable = () if names == "none" else tuple(name.strip() for name in names.split(","))
            elif (found := _CAPS.match(text)) is not None:
                entries = found.group("entries")
                run.caps[found.group("name")] = IdentityCaps(
                    int(found.group("positions")), None if entries == "none" else int(entries),
                    timedelta(minutes=int(found.group("minutes"))),
                )
            elif (found := _NET_LIQ.match(text)) is not None:
                value = _decimal(found.group("value"))
                if value is not None and value > 0:
                    run.net_liquidation.append(value)
            elif (found := _SESSION_END.match(text)) is not None:
                run.session_end_pnl = _decimal(found.group("value"))
        if line.kind in {"INFO", "CRITICAL"} and _FENCED.match(text):
            run.fenced.append(line)


def read_bridge_section(logs: Path, day: date) -> BridgeSection:
    """Every bridge process of ``day`` in the bridge logs, in file order."""
    files = log_files(logs, BRIDGE_LOG)
    if not files:
        raise InputsMissing(f"no {BRIDGE_LOG} in {logs}")
    runs: list[BridgeRun] = []
    used: list[str] = []
    for path in files:
        for block in _blocks(_lines(path)):
            if block.day != day:
                continue
            run = BridgeRun(index=len(runs), source=path.name, first_line=block.first_line,
                            banner_at=block.banner_at)
            for number, raw in block.raw:
                event = _EVENT.match(raw)
                if event is None:
                    run.lines.append(LogLine(path.name, number, run.index, None, "", raw))
                    continue
                clock = datetime.strptime(event.group("clock"), "%H:%M:%S").time()
                run.lines.append(LogLine(path.name, number, run.index, clock, event.group("kind"),
                                         " ".join(event.group("text").split())))
            _summarize(run)
            runs.append(run)
            if path.name not in used:
                used.append(path.name)
    if not runs:
        raise InputsMissing(f"no {BRIDGE_LOG} section for {day.isoformat()} in {logs}")
    return BridgeSection(session_date=day, runs=runs, files=used)


# --- reading: supervisor.log ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupervisorEvent:
    at: datetime
    event: str  # CHILD_START, CHILD_EXIT, LAUNCH_REFUSED, SIGNAL, CHILD_RESTART_STORM, ...
    code: str | None
    restart: str | None
    ref: str
    text: str


def read_supervisor_bridge(logs: Path, day: date) -> tuple[list[SupervisorEvent], int]:
    """The bridge child's supervisor events dated ``day`` in New York; and the files read."""
    files = log_files(logs, SUPERVISOR_LOG)
    if not files:
        raise InputsMissing(f"no {SUPERVISOR_LOG} in {logs}")
    events: list[SupervisorEvent] = []
    for path in files:
        lines = _lines(path)
        # A carried copy's original is in the archive: count each line once (every
        # supervisor line carries its own time, so equal text means the same line).
        kept = Counter(without_carried(lines))
        for number, raw in enumerate(lines, 1):
            if kept[raw] <= 0:
                continue
            kept[raw] -= 1
            found = _SUPERVISOR.match(raw.strip())
            at = line_time(raw)
            if found is None or at is None or at.astimezone(NY).date() != day:
                continue
            rest = found.group("rest") or ""
            code = _CODE.search(rest)
            restart = _RESTART.search(rest)
            events.append(SupervisorEvent(
                at=at, event=found.group("event"), code=code.group("code") if code else None,
                restart=restart.group("restart") if restart else None, ref=f"{path.name}:{number}",
                text=raw.strip(),
            ))
    return events, len(files)


# --- reading: the bridge state --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StateBracket:
    decision_id: str
    symbol: str
    strategy_name: str
    qty: int
    entry: Decimal | None
    stop: Decimal | None
    parent: int | None
    status: str
    skipped: bool
    skip_reason: str
    filled: int
    exit_filled: int
    realized_pnl: Decimal
    sent_at: datetime | None

    @property
    def sent(self) -> bool:
        return self.parent is not None

    @property
    def net_held(self) -> int:
        return self.filled - self.exit_filled


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    number = _decimal(value)
    return int(number) if number is not None and number == number.to_integral_value() else default


def _bracket(decision_id: str, item: Mapping[str, Any]) -> StateBracket:
    parent = item.get("parent")
    return StateBracket(
        decision_id=decision_id,
        symbol=str(item.get("symbol") or "?"),
        strategy_name=str(item.get("strategy_name") or "").strip(),
        qty=_int(item.get("qty")),
        entry=_decimal(item.get("entry")),
        stop=_decimal(item.get("stop")),
        parent=parent if isinstance(parent, int) and not isinstance(parent, bool) else None,
        status=str(item.get("status") or ""),
        skipped=bool(item.get("skipped")),
        skip_reason=str(item.get("skip_reason") or ""),
        filled=_int(item.get("filled")),
        exit_filled=_int(item.get("exit_filled")),
        realized_pnl=_decimal(item.get("realized_pnl")) or Decimal("0"),
        sent_at=_iso(str(item.get("sent_at") or "")),
    )


def state_path(data: Path, day: date) -> Path:
    return data / BRIDGE_STATE_DIRECTORY / f"{day.isoformat()}.json"


def read_state(data: Path, day: date) -> dict[str, StateBracket]:
    """The bridge's state of ``day`` (never the dashboard mirror, never another date's file)."""
    path = state_path(data, day)
    if not path.exists():
        raise InputsMissing(f"no bridge state {BRIDGE_STATE_DIRECTORY}/{path.name} in {data}")
    try:
        document = json.loads(_read_bytes(path, MAX_STATE_BYTES).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InputsMissing(f"bridge state {path.name} is unreadable ({type(exc).__name__})") from exc
    schema = document.get("schema") if isinstance(document, dict) else None
    if not isinstance(schema, str) or not schema.startswith(STATE_SCHEMA_PREFIX):
        raise InputsMissing(f"bridge state {path.name} is not a bridge state document")
    if document.get("session_date") not in (None, day.isoformat()):
        raise InputsMissing(f"bridge state {path.name} holds session {document.get('session_date')}")
    submitted = document.get("submitted")
    if not isinstance(submitted, dict):
        raise InputsMissing(f"bridge state {path.name} has no submitted map")
    return {str(key): _bracket(str(key), item) for key, item in submitted.items() if isinstance(item, dict)}


# --- reading: the database (read-only) ---------------------------------------------------


def open_read_only(db: Path) -> sqlite3.Connection:
    """``file:<db>?mode=ro`` plus ``PRAGMA query_only``: no write or migration can pass."""
    path = db.expanduser()
    if not _regular_file(path.resolve()):
        raise InputsMissing(f"no database at {db}")
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        raise InputsMissing(f"database {db} unreadable ({type(exc).__name__}: {exc})") from exc
    return connection


@dataclass(frozen=True, slots=True)
class Intent:
    quantity: int
    entry: Decimal
    stop: Decimal
    target: Decimal


def parse_intent(payload_json: str) -> Intent | None:
    """The bridge's own reading of a decision's trade intent (``paper_bridge.parse_intent``;
    a test pins the two equal): ``risk.intent`` quantity, entry, stop and target."""
    payload = json.loads(payload_json)
    risk = payload.get("risk") if isinstance(payload, dict) else None
    intent = risk.get("intent") if isinstance(risk, dict) else None
    if not isinstance(intent, dict) or not intent:
        return None
    quantity = Decimal(str(intent["quantity"]))
    if not quantity.is_finite() or quantity != quantity.to_integral_value():
        raise ValueError("trade intent quantity is not a whole number")
    return Intent(
        quantity=int(quantity),
        entry=Decimal(str(intent["entry_limit"])),
        stop=Decimal(str(intent["stop_price"])),
        target=Decimal(str(intent["target_price"])),
    )


def strategy_name_from_spec(spec_json: object) -> str:
    """The ledger name the bridge gates on (``paper_bridge.strategy_name_from_spec``)."""
    if not isinstance(spec_json, str):
        return ""
    try:
        spec = json.loads(spec_json)
    except ValueError:
        return ""
    name = spec.get("name") if isinstance(spec, dict) else None
    return name.strip() if isinstance(name, str) else ""


@dataclass(frozen=True, slots=True)
class LedgerDecision:
    decision_id: str
    run_id: str
    symbol: str
    created_at: str
    strategy_name: str
    intent: Intent | None
    problem: str
    policy_fingerprint: str
    portfolio_fingerprint: str


def read_decisions(connection: sqlite3.Connection, day: date) -> dict[str, LedgerDecision]:
    """Every ``shadow_accepted`` decision of ``day``, read with the bridge's own query."""
    decisions: dict[str, LedgerDecision] = {}
    for row in connection.execute(DECISIONS_SQL, (day.isoformat(),)).fetchall():
        payload_json = str(row[5])
        intent: Intent | None = None
        problem = ""
        risk: Any = {}
        try:
            intent = parse_intent(payload_json)
            payload = json.loads(payload_json)
            risk = payload.get("risk") if isinstance(payload, dict) else {}
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            problem = f"intent unreadable ({type(exc).__name__})"
        risk = risk if isinstance(risk, dict) else {}
        decisions[str(row[0])] = LedgerDecision(
            decision_id=str(row[0]), run_id=str(row[1]), symbol=str(row[2]), created_at=str(row[6]),
            strategy_name=strategy_name_from_spec(row[7]), intent=intent,
            problem=problem or ("" if intent is not None else "no trade intent"),
            policy_fingerprint=str(risk.get("policy_fingerprint") or ""),
            portfolio_fingerprint=str(risk.get("portfolio_fingerprint") or ""),
        )
    return decisions


@dataclass(frozen=True, slots=True)
class FenceRecord:
    name: str
    stopped: bool
    reason: str
    updated_at: datetime | None
    updated_text: str

    @property
    def identity(self) -> str | None:
        return self.name[len(IDENTITY_FENCE_PREFIX):] if self.name.startswith(IDENTITY_FENCE_PREFIX) else None

    @property
    def label(self) -> str:
        return "global PAPER stop" if self.name == GLOBAL_FENCE else self.name


def read_fences(connection: sqlite3.Connection) -> list[FenceRecord]:
    return [
        FenceRecord(name=str(name), stopped=bool(_int(stopped)), reason=str(reason),
                    updated_at=_iso(str(updated)), updated_text=str(updated))
        for name, stopped, reason, updated in connection.execute(FENCES_SQL).fetchall()
    ]


def _account_snapshots(connection: sqlite3.Connection, run_id: str) -> list[AccountSnapshot]:
    snapshots: list[AccountSnapshot] = []
    try:
        rows = connection.execute(ACCOUNT_SNAPSHOTS_SQL, (run_id,)).fetchall()
    except sqlite3.Error:
        return snapshots  # no snapshot table: the inputs are simply not recorded
    for (payload_json,) in rows:
        try:
            snapshots.append(AccountSnapshot.model_validate(json.loads(str(payload_json))))
        except ValueError:
            continue  # an unreadable snapshot proves nothing: the inputs stay unrecorded
    return snapshots


# --- the replay ---------------------------------------------------------------------


@dataclass(slots=True)
class Entry:
    """One SENT line and everything the section and the state say about it."""

    line: LogLine
    symbol: str
    identity: str
    qty: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    parent: int
    net_liquidation: Decimal | None
    bracket: StateBracket | None = None
    decision: LedgerDecision | None = None
    fills: list[LogLine] = field(default_factory=list)
    exits: list[LogLine] = field(default_factory=list)
    realized: Decimal = Decimal("0")
    closed: LogLine | None = None
    cancelled: LogLine | None = None
    open: bool = True

    def sent_at(self, day: date) -> datetime | None:
        if self.bracket is not None and self.bracket.sent_at is not None:
            return self.bracket.sent_at.astimezone(NY)
        return self.line.at(day)

    @property
    def name(self) -> str:
        return f"{self.identity} {self.symbol} (orders {self.parent}-{self.parent + 2})"


@dataclass(slots=True)
class Skip:
    line: LogLine
    symbol: str
    label: str | None
    reason: str


@dataclass(slots=True)
class Timeline:
    entries: list[Entry] = field(default_factory=list)
    unattributed: list[tuple[LogLine, str]] = field(default_factory=list)
    attributed: dict[str, int] = field(default_factory=lambda: {"FILL": 0, "EXIT": 0, "CLOSED": 0})
    max_open: dict[str, tuple[int, LogLine]] = field(default_factory=dict)
    max_total: tuple[int, LogLine | None] = (0, None)
    skips: list[Skip] = field(default_factory=list)
    flats: list[LogLine] = field(default_factory=list)
    not_flat: list[LogLine] = field(default_factory=list)
    manual: int = 0
    loss_crossed: LogLine | None = None
    loss_violations: list[Entry] = field(default_factory=list)

    @property
    def realized(self) -> Decimal:
        return sum((entry.realized for entry in self.entries), Decimal("0"))


def _loss_limit(net_liquidation: Decimal) -> Decimal:
    return net_liquidation * MAX_DAILY_LOSS_PCT / 100


def replay(section: BridgeSection, state: Mapping[str, StateBracket],
           decisions: Mapping[str, LedgerDecision]) -> Timeline:
    """Walk the section in order: SENT opens a bracket, CLOSED or CANCELLED ends it."""
    timeline = Timeline()
    by_parent = {bracket.parent: bracket for bracket in state.values() if bracket.parent is not None}
    latest: dict[str, Entry] = {}
    net_liquidation: Decimal | None = None
    run = -1
    for line in section.lines():
        if line.run != run:  # a new process knows no net liquidation until it logs one
            run, net_liquidation = line.run, None
        kind, text = line.kind, line.text
        if kind == "INFO":
            found = _NET_LIQ.match(text)
            if found is not None:
                net_liquidation = _decimal(found.group("value"))
            continue
        if kind == "MANUAL":
            timeline.manual += 1
            continue
        if kind == "SENT":
            sent = _SENT.match(text)
            if sent is None:
                timeline.unattributed.append((line, "SENT line not in the bridge's format"))
                continue
            entry = Entry(
                line=line, symbol=sent.group("symbol"), identity=sent.group("label").strip(),
                qty=int(sent.group("qty")), entry=Decimal(sent.group("entry")), stop=Decimal(sent.group("stop")),
                target=Decimal(sent.group("target")), parent=int(sent.group("parent")),
                net_liquidation=net_liquidation,
            )
            bracket = by_parent.get(entry.parent)
            if bracket is not None and bracket.symbol == entry.symbol:
                entry.bracket = bracket
                entry.decision = decisions.get(bracket.decision_id)
            if net_liquidation is not None and -timeline.realized >= _loss_limit(net_liquidation):
                timeline.loss_violations.append(entry)
            timeline.entries.append(entry)
            latest[entry.symbol] = entry
            _count_open(timeline, entry)
            continue
        if kind in {"FILL", "EXIT", "CLOSED", "CANCELLED"}:
            pattern = {"FILL": _FILL, "EXIT": _EXIT, "CLOSED": _CLOSED, "CANCELLED": _CANCELLED}[kind]
            found = pattern.match(text)
            owner = latest.get(found.group("symbol")) if found is not None else None
            if found is None or owner is None:
                timeline.unattributed.append((line, f"{kind} without a SENT for its symbol"))
                continue
            if kind == "FILL":
                if found.group("label").strip() != owner.identity:
                    timeline.unattributed.append((line, f"FILL names {found.group('label')!r}, its SENT {owner.identity}"))
                    continue
                owner.fills.append(line)
                if not owner.open and owner.closed is None:  # a fill that raced the cancel holds shares
                    owner.open = True
                    _count_open(timeline, owner)
            elif kind == "EXIT":
                owner.exits.append(line)
                owner.realized = Decimal(found.group("pnl"))
                if (net_liquidation is not None and timeline.loss_crossed is None
                        and -timeline.realized >= _loss_limit(net_liquidation)):
                    timeline.loss_crossed = line
            elif kind == "CLOSED":
                owner.closed, owner.open = line, False
            else:
                owner.cancelled, owner.open = line, False
            if kind != "CANCELLED":
                timeline.attributed[kind] += 1
            continue
        if kind == "SKIP":
            found = _SKIP.match(text)
            if found is not None:
                timeline.skips.append(Skip(line, found.group("symbol"), found.group("label"), found.group("reason")))
            continue
        if kind == "FLAT":
            timeline.flats.append(line)
            continue
        if kind == "CRITICAL":
            if any(phrase in text for phrase in NOT_FLAT_PHRASES):
                timeline.not_flat.append(line)
            found = _NOT_FOUND.match(text)
            owner = latest.get(found.group("symbol")) if found is not None else None
            # needs_attention holds a slot only while it may hold shares (the bridge's holds_or_may_hold).
            if owner is not None and owner.bracket is not None and owner.bracket.filled == 0:
                owner.open = False
    return timeline


def _count_open(timeline: Timeline, entry: Entry) -> None:
    mine = sum(1 for item in timeline.entries if item.open and item.identity == entry.identity)
    if mine > timeline.max_open.get(entry.identity, (0, entry.line))[0]:
        timeline.max_open[entry.identity] = (mine, entry.line)
    total = sum(1 for item in timeline.entries if item.open)
    if total > timeline.max_total[0]:
        timeline.max_total = (total, entry.line)


# --- the report ---------------------------------------------------------------------


@dataclass(slots=True)
class RuleResult:
    number: int
    title: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionReport:
    session_date: date
    inputs: list[str] = field(default_factory=list)
    rules: list[RuleResult] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.missing:
            return EXIT_INPUTS_MISSING
        return EXIT_FAILED if any(rule.status == FAIL for rule in self.rules) else EXIT_PASSED

    def render(self) -> str:
        day = self.session_date.isoformat()
        if self.missing:
            lines = [f"Session check {day}: INPUTS MISSING (exit {EXIT_INPUTS_MISSING}), no verdict"]
            lines += [f"  - {problem}" for problem in self.missing]
            lines += [f"  {item}" for item in self.inputs]
            return mask_accounts("\n".join(lines)) + "\n"
        failed = sum(rule.status == FAIL for rule in self.rules)
        warned = sum(rule.status == WARN for rule in self.rules)
        verdict = FAIL if failed else PASS
        notes = [f"{count} {word}{'s' if count != 1 else ''}"
                 for count, word in ((failed, "failure"), (warned, "warning")) if count]
        lines = [f"Session check {day}: {verdict}" + (f" ({', '.join(notes)})" if notes else "")
                 + f", exit {self.exit_code} (read-only)"]
        lines += [f"{rule.status}  rule {rule.number} {rule.title}: {rule.summary}" for rule in self.rules]
        lines += ["", "Inputs", *(f"  {item}" for item in self.inputs), "", "Details"]
        for rule in self.rules:
            lines.append(f"  rule {rule.number} {rule.title} ({rule.status})")
            lines += [f"    {detail}" for detail in rule.details] or ["    (nothing to add)"]
        return mask_accounts("\n".join(lines)) + "\n"


def _plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if Decimal(text) == 0 else text


def _factor(value: Decimal) -> str:
    return f"x{_plain(value)}"


def _percent(part: Decimal, whole: Decimal) -> str:
    return f"{(part * 100 / whole).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)}%"


def entry_deadline(identity: str, closes_at: datetime) -> datetime:
    """The bridge's last entry moment for ``identity``: its caps row's offset from the
    real close, never later than the session's own last entry (close - 30 min)."""
    session_last = closes_at - SESSION_LAST_ENTRY_BEFORE_CLOSE
    caps = BRIDGE_IDENTITY_CAPS.get(identity)
    return session_last if caps is None else min(session_last, closes_at - caps.last_entry_before_close)


@dataclass(slots=True)
class _Context:
    day: date
    closes_at: datetime
    section: BridgeSection
    supervisor: list[SupervisorEvent]
    state: dict[str, StateBracket]
    decisions: dict[str, LedgerDecision]
    fences: list[FenceRecord]
    timeline: Timeline
    connection: sqlite3.Connection

    @property
    def logged_sendable(self) -> set[str] | None:
        sets = [set(run.sendable) for run in self.section.runs if run.sendable is not None]
        return set().union(*sets) if sets else None

    @property
    def net_liquidations(self) -> list[Decimal]:
        return [value for run in self.section.runs for value in run.net_liquidation]


def _rule_identities(context: _Context) -> RuleResult:
    approved = set(SENDABLE_IDENTITIES)
    logged = context.logged_sendable
    in_force = approved if logged is None else logged
    failures: list[str] = []
    details: list[str] = []
    if logged is None:
        details.append("the bridge logged no 'sendable identities' line (older than v7a): checked against"
                       f" the approved set {', '.join(sorted(approved))}")
    else:
        details.append(f"bridge sendable identities this day: {', '.join(sorted(logged)) or 'none'}")
        if logged - approved:
            failures.append(f"the bridge's sendable set names {', '.join(sorted(logged - approved))},"
                            " outside SENDABLE_IDENTITIES")
    sent_by: dict[str, int] = {}
    for entry in context.timeline.entries:
        sent_by[entry.identity] = sent_by.get(entry.identity, 0) + 1
        where = entry.line.evidence()
        if entry.identity not in approved or entry.identity not in in_force:
            failures.append(f"SENT for a shadow-only name {entry.identity!r}: {where}")
        if entry.bracket is None:
            failures.append(f"SENT without a state bracket for orders {entry.parent}-{entry.parent + 2}: {where}")
            continue
        if entry.bracket.strategy_name != entry.identity:
            failures.append(f"state bracket {entry.bracket.decision_id[:12]} names"
                            f" {entry.bracket.strategy_name!r}, its SENT {entry.identity}: {where}")
        if entry.decision is None:
            failures.append(f"decision {entry.bracket.decision_id[:12]} is not a shadow_accepted decision of"
                            f" {context.day} in the ledger: {where}")
        elif entry.decision.strategy_name != entry.identity:
            failures.append(f"ledger decision {entry.decision.decision_id[:12]} names"
                            f" {entry.decision.strategy_name!r}, its SENT {entry.identity}: {where}")
        details.append(f"SENT {entry.name} at {entry.line.stamp}: state and ledger name"
                       f" {entry.bracket.strategy_name} (decision {entry.bracket.decision_id[:12]})")
    sent_parents = {entry.parent for entry in context.timeline.entries}
    for bracket in context.state.values():
        if bracket.sent and bracket.parent not in sent_parents:
            failures.append(f"state bracket {bracket.decision_id[:12]} ({bracket.strategy_name} {bracket.symbol},"
                            f" order {bracket.parent}) has no SENT line in the section")
        if bracket.sent and bracket.strategy_name not in approved:
            failures.append(f"state bracket {bracket.decision_id[:12]} {bracket.symbol} was sent for the"
                            f" shadow-only name {bracket.strategy_name!r}")
        if (logged is not None and not bracket.sent and bracket.strategy_name in in_force
                and bracket.skip_reason == SHADOW_ONLY_REASON):
            failures.append(f"state bracket {bracket.decision_id[:12]} {bracket.symbol}: sendable"
                            f" {bracket.strategy_name} skipped as shadow-only")
    shadow_skips: dict[str, int] = {}
    for skip in context.timeline.skips:
        if skip.label is None:
            continue
        if skip.label not in in_force:
            shadow_skips[skip.label] = shadow_skips.get(skip.label, 0) + 1
        elif logged is not None and skip.reason == SHADOW_ONLY_REASON:
            failures.append(f"sendable {skip.label} skipped as shadow-only: {skip.line.evidence()}")
    if shadow_skips:
        details.append("shadow-only names, SKIP lines only: "
                       + ", ".join(f"{name} x{count}" for name, count in sorted(shadow_skips.items())))
    sent_text = ", ".join(f"{name} x{count}" for name, count in sorted(sent_by.items())) or "none"
    if failures:
        return RuleResult(1, "identities", FAIL, f"{len(failures)} problem(s); first: {failures[0]}",
                          failures + details)
    shadow = sum(shadow_skips.values())
    return RuleResult(1, "identities", PASS,
                      f"{len(context.timeline.entries)} SENT, all sendable ({sent_text}); shadow-only names only"
                      f" as SKIP ({shadow} line{'s' if shadow != 1 else ''}); sendable set"
                      f" {', '.join(sorted(in_force)) or 'none'}", details)


def _rule_attribution(context: _Context) -> RuleResult:
    timeline = context.timeline
    failures = [f"unattributed: {reason}: {line.evidence()}" for line, reason in timeline.unattributed]
    details: list[str] = []
    for entry in timeline.entries:
        if entry.bracket is None:
            continue
        if entry.bracket.filled > 0 and not entry.fills:
            failures.append(f"{entry.name}: the state holds {entry.bracket.filled} filled shares, no FILL line")
        if entry.fills and entry.bracket.filled == 0:
            failures.append(f"{entry.name}: FILL lines but the state holds no filled shares")
        if entry.exits and entry.bracket.exit_filled == 0:
            failures.append(f"{entry.name}: EXIT lines but the state holds no exit fills")
        if entry.bracket.exit_filled > 0 and not entry.exits:
            failures.append(f"{entry.name}: the state holds {entry.bracket.exit_filled} exit fills, no EXIT line")
        details.append(f"{entry.name}: {len(entry.fills)} FILL, {len(entry.exits)} EXIT,"
                       f" {'CLOSED ' + entry.closed.stamp if entry.closed else 'CANCELLED' if entry.cancelled else 'not closed'};"
                       f" state {entry.bracket.status} {entry.bracket.filled}/{entry.bracket.qty} in,"
                       f" {entry.bracket.exit_filled} out")
    failures += [f"not flat: {line.evidence()}" for line in timeline.not_flat]
    for bracket in context.state.values():
        if bracket.status in {"open", "needs_attention"} and bracket.net_held != 0:
            failures.append(f"state: {bracket.symbol} ({bracket.strategy_name}) is {bracket.status} holding"
                            f" {bracket.net_held} sh at the end of the session")
    flatten_at = context.closes_at - FLATTEN_BEFORE_CLOSE
    flats = [line for line in timeline.flats if (at := line.at(context.day)) is not None and at >= flatten_at]
    if flats:
        details.append(f"FLAT after the {flatten_at:%H:%M} ET flatten: {flats[-1].evidence()}")
    else:
        failures.append(f"no FLAT line at or after the {flatten_at:%H:%M} ET flatten")
    stops = [run.stopped for run in context.section.runs if run.stopped is not None]
    last_code = next((run.stopped_code for run in reversed(context.section.runs) if run.stopped is not None), None)
    if last_code is None:
        failures.append("no 'bridge stopped (exit N)' line in the section")
    elif last_code != 0:
        failures.append(f"the last bridge process stopped with exit {last_code}: {stops[-1].evidence()}")
    else:
        details.append(f"last stop: {stops[-1].evidence()}")
    restarts = [run.stopped_code for run in context.section.runs[:-1] if run.stopped_code not in (None, 0)]
    if restarts:
        details.append(f"{len(restarts)} earlier bridge process(es) stopped for a supervised restart")
    starts = [event for event in context.supervisor if event.event == "CHILD_START"]
    exits = [event for event in context.supervisor if event.event == "CHILD_EXIT"]
    banners = sum(1 for run in context.section.runs if run.banner_at is not None)
    warnings: list[str] = []
    if len(starts) != banners:
        warnings.append(f"supervisor.log has {len(starts)} bridge starts on {context.day}, bridge.log"
                        f" {banners} supervisor banners")
    if exits and (exits[-1].code != "0" or (exits[-1].restart or "").lower() not in {"none", ""}):
        failures.append(f"supervisor: the bridge's last exit on {context.day} was not final code 0:"
                        f" {exits[-1].ref} {exits[-1].text}")
    elif exits:
        details.append(f"supervisor: {len(starts)} bridge starts, last exit {exits[-1].ref} {exits[-1].text}")
    else:
        warnings.append(f"supervisor.log has no bridge exit on {context.day}")
    failures += [f"supervisor refused to launch the bridge: {event.ref} {event.text}"
                 for event in context.supervisor if event.event == "LAUNCH_REFUSED"]
    if timeline.manual:
        details.append(f"{timeline.manual} MANUAL line(s): owner orders, outside the identity rules")
    counts = timeline.attributed
    summary = (f"{counts['FILL']} FILL, {counts['EXIT']} EXIT and {counts['CLOSED']} CLOSED lines attributed to"
               f" their SENT; {'FLAT ' + flats[-1].stamp if flats else 'no FLAT'}; bridge stopped"
               f" (exit {last_code if last_code is not None else '?'})")
    if failures:
        return RuleResult(2, "attribution and close-out", FAIL,
                          f"{len(failures)} problem(s); first: {failures[0]}", failures + warnings + details)
    status = WARN if warnings else PASS
    return RuleResult(2, "attribution and close-out", status, summary, warnings + details)


def _caps_text(caps: IdentityCaps) -> str:
    entries = "none" if caps.max_entries_per_day is None else str(caps.max_entries_per_day)
    minutes = int(caps.last_entry_before_close.total_seconds() // 60)
    return f"positions={caps.max_positions} entries/day={entries} last_entry=close-{minutes}m"


def _rule_caps(context: _Context) -> RuleResult:
    timeline = context.timeline
    failures: list[str] = []
    details: list[str] = []
    for run in context.section.runs:
        for name, caps in run.caps.items():
            approved = BRIDGE_IDENTITY_CAPS.get(name)
            if approved != caps:
                failures.append(f"the bridge logged caps {name} {_caps_text(caps)}, the approved row is"
                                f" {_caps_text(approved) if approved else 'missing'}: process at"
                                f" {run.source}:{run.first_line}")
    attempts: dict[str, list[Entry]] = {}
    for entry in timeline.entries:
        attempts.setdefault(entry.identity, []).append(entry)
    state_attempts: dict[str, int] = {}
    for bracket in context.state.values():
        if bracket.sent:
            state_attempts[bracket.strategy_name] = state_attempts.get(bracket.strategy_name, 0) + 1
    for identity in sorted(set(attempts) | set(state_attempts) | set(BRIDGE_IDENTITY_CAPS)):
        caps = BRIDGE_IDENTITY_CAPS.get(identity)
        entries = attempts.get(identity, [])
        if caps is None:
            if entries or state_attempts.get(identity):
                failures.append(f"{identity}: entries sent but it has no approved caps row")
            continue
        count = max(len(entries), state_attempts.get(identity, 0))
        deadline = entry_deadline(identity, context.closes_at)
        peak, peak_line = timeline.max_open.get(identity, (0, None))
        if peak > caps.max_positions:
            failures.append(f"{identity}: {peak} brackets open at once, cap {caps.max_positions}:"
                            f" {peak_line.evidence() if peak_line else ''}")
        if caps.max_entries_per_day is not None and count > caps.max_entries_per_day:
            failures.append(f"{identity}: {count} entries sent, cap {caps.max_entries_per_day}/day"
                            f" (attempts count): {entries[-1].line.evidence() if entries else 'state only'}")
        for entry in entries:
            sent_at = entry.sent_at(context.day)
            if sent_at is None:
                failures.append(f"{entry.name}: no send time in the state or the log")
            elif sent_at >= deadline:
                failures.append(f"{entry.name}: sent {sent_at:%H:%M:%S} ET, at or after its last entry"
                                f" {deadline:%H:%M} ET: {entry.line.evidence()}")
        limit = "none" if caps.max_entries_per_day is None else str(caps.max_entries_per_day)
        last = max((at for entry in entries if (at := entry.sent_at(context.day)) is not None), default=None)
        details.append(f"{identity}: open at most {peak}/{caps.max_positions}, entries {count}/{limit},"
                       f" last entry {f'{last:%H:%M:%S} ET' if last else 'none'} (cutoff {deadline:%H:%M} ET)")
    total_cap = sum(caps.max_positions for caps in BRIDGE_IDENTITY_CAPS.values())
    if timeline.max_total[0] > total_cap:
        failures.append(f"{timeline.max_total[0]} brackets open at once across identities, cap {total_cap}")
    details.append(f"all identities: open at most {timeline.max_total[0]}/{total_cap}")
    if failures:
        return RuleResult(3, "caps", FAIL, f"{len(failures)} problem(s); first: {failures[0]}", failures + details)
    sent = [line for line in details if " entries 0/" not in line and not line.startswith("all identities")]
    return RuleResult(3, "caps", PASS, "; ".join(sent) or "no entries", details)


@dataclass(frozen=True, slots=True)
class _Prices:
    """The two setup prices ``policy_quantity`` reads (entry_limit, stop_price)."""

    entry_limit: Decimal
    stop_price: Decimal


def _policy_quantity(portfolio: PortfolioSnapshot, entry: Decimal, stop: Decimal) -> int:
    return policy_quantity(policy=RiskPolicy(), portfolio=portfolio,
                           setup=cast(RuleSetup, _Prices(entry_limit=entry, stop_price=stop)))


def _recorded_portfolio(context: _Context, decision: LedgerDecision) -> PortfolioSnapshot | None:
    """The portfolio the decision was sized from, when an account snapshot of its run rebuilds it
    to the decision's own portfolio fingerprint (the session runner's view: no owned positions,
    this run's earlier accepted symbols pending)."""
    if not decision.portfolio_fingerprint:
        return None
    snapshots = _account_snapshots(context.connection, decision.run_id)
    if not snapshots:
        return None
    earlier: list[str] = []
    for other in context.decisions.values():
        if other.run_id == decision.run_id and (other.created_at, other.decision_id) < (
                decision.created_at, decision.decision_id) and other.symbol not in earlier:
            earlier.append(other.symbol)
    pending_sets = {tuple(sorted(earlier[:count])) for count in range(len(earlier) + 1)}
    for account in snapshots:
        for pending in sorted(pending_sets):
            portfolio = portfolio_from_account(account, owned_con_ids=frozenset(), pending_symbols=pending)
            if portfolio.fingerprint == decision.portfolio_fingerprint:
                return portfolio
    return None


@dataclass(slots=True)
class _Sized:
    """One SENT's size verdict: its failures, its evidence line, and what stayed unchecked."""

    failures: list[str] = field(default_factory=list)
    detail: str = ""
    exact: bool = False
    unchecked: list[str] = field(default_factory=list)


def _size_entry(context: _Context, entry: Entry) -> _Sized:
    sized = _Sized()
    decision, bracket = entry.decision, entry.bracket
    where = entry.line.evidence()
    if bracket is not None and bracket.qty != entry.qty:
        sized.failures.append(f"{entry.name}: SENT {entry.qty} sh, the state {bracket.qty}: {where}")
    if decision is None or decision.intent is None:
        problem = "no ledger decision" if decision is None else decision.problem
        sized.failures.append(f"{entry.name}: its shares cannot be traced to a decision intent ({problem}): {where}")
        return sized
    intent = decision.intent
    if (intent.quantity, intent.entry, intent.stop, intent.target) != (entry.qty, entry.entry, entry.stop,
                                                                       entry.target):
        sized.failures.append(f"{entry.name}: SENT {entry.qty} @ {entry.entry}/{entry.stop}/{entry.target}, its"
                              f" decision's intent {intent.quantity} @ {intent.entry}/{intent.stop}/{intent.target}")
    row = IDENTITY_SIZE_MULTIPLIER.get(entry.identity)
    if row is None:
        sized.failures.append(f"{entry.name}: {entry.identity} has no identity size row")
        return sized
    parts = [f"{intent.quantity} sh = SENT = intent" + (" = state" if bracket is not None else "")]
    net_liq = entry.net_liquidation
    if net_liq is None:
        sized.unchecked.append(f"{entry.name}: the bridge logged no net liquidation in this process:"
                               " order ceilings unchecked")
    else:
        notional, risk = entry.qty * entry.entry, entry.qty * (entry.entry - entry.stop)
        if notional > net_liq * MAX_ORDER_NOTIONAL_PCT / 100:
            sized.failures.append(f"{entry.name}: notional {notional} is above {MAX_ORDER_NOTIONAL_PCT}% of net"
                                  f" liquidation {net_liq}")
        if risk > net_liq * MAX_ORDER_RISK_PCT / 100:
            sized.failures.append(f"{entry.name}: risk {risk} is above {MAX_ORDER_RISK_PCT}% of net liquidation"
                                  f" {net_liq}")
        parts.append(f"notional {_percent(notional, net_liq)}, risk {_percent(risk, net_liq)} of net liquidation"
                     f" {net_liq} (ceilings {MAX_ORDER_NOTIONAL_PCT}%, {MAX_ORDER_RISK_PCT}%)")
    default_policy = decision.policy_fingerprint == RiskPolicy().fingerprint()
    recorded = _recorded_portfolio(context, decision) if default_policy else None
    if recorded is not None:
        policy = _policy_quantity(recorded, entry.entry, entry.stop)
        expected = scaled_quantity(policy, row)
        sized.exact = expected == intent.quantity
        if not sized.exact:
            sized.failures.append(f"{entry.name}: {intent.quantity} sh, but floor(policy {policy} x {_factor(row)})"
                                  f" = {expected} at the recorded net liquidation {recorded.net_liquidation}")
        parts.insert(1, f"floor(policy {policy} x {_factor(row)}) = {expected} at the recorded net liquidation"
                        f" {recorded.net_liquidation}")
        sized.detail = f"{entry.name}: " + "; ".join(parts)
        return sized
    parts.insert(1, "inputs not recorded (no account snapshot of run"
                    f" {decision.run_id} rebuilds the decision's portfolio fingerprint"
                    + ("" if default_policy else "; its policy is not the default RiskPolicy") + ")")
    logged = context.net_liquidations
    reference = net_liq or (max(logged) if logged else None)
    if reference is None or not default_policy:
        sized.unchecked.append(f"{entry.name}: identity-row bound unchecked"
                               f" ({'no net liquidation logged' if reference is None else 'policy not the default'})")
        sized.detail = f"{entry.name}: " + "; ".join(parts)
        return sized
    at = entry.sent_at(context.day) or datetime.combine(context.day, time(12), tzinfo=NY)
    high = max(logged) * (1 + NET_LIQUIDATION_BAND)
    bound = scaled_quantity(
        _policy_quantity(PortfolioSnapshot(observed_at=at, net_liquidation=high), entry.entry, entry.stop), row
    )
    policy = _policy_quantity(PortfolioSnapshot(observed_at=at, net_liquidation=reference), entry.entry, entry.stop)
    implied = f"x{(Decimal(intent.quantity) / policy).quantize(Decimal('0.01'))}" if policy > 0 else "x?"
    band = f"+{_plain(NET_LIQUIDATION_BAND * 100)}%"
    if intent.quantity > bound:
        sized.failures.append(f"{entry.name}: {intent.quantity} sh is above its {_factor(row)} row: at most {bound}"
                              f" sh even at net liquidation {high.quantize(Decimal('0.01'))} (logged {band}); about"
                              f" {implied} of the policy size {policy} at {reference}")
    parts.insert(2, f"about {implied} of the policy size {policy} at the logged net liquidation {reference};"
                    f" row {_factor(row)} allows at most {bound} at {band}")
    sized.detail = f"{entry.name}: " + "; ".join(parts)
    return sized


def _rule_size(context: _Context) -> RuleResult:
    failures: list[str] = []
    unchecked: list[str] = []
    details = [f"the bridge's ceiling refused a decision: {skip.line.evidence()}"
               for skip in context.timeline.skips if "exceeds" in skip.reason and "net liquidation" in skip.reason]
    exact = 0
    for entry in context.timeline.entries:
        sized = _size_entry(context, entry)
        failures += sized.failures
        unchecked += sized.unchecked
        exact += sized.exact
        if sized.detail:
            details.append(sized.detail)
    entries = len(context.timeline.entries)
    if failures:
        return RuleResult(4, "size", FAIL, f"{len(failures)} problem(s); first: {failures[0]}",
                          failures + unchecked + details)
    if not entries:
        return RuleResult(4, "size", PASS, "no SENT: nothing was sized", details)
    if exact < entries:
        rest = f"; {len(unchecked)} check(s) could not run, see details" if unchecked else ""
        return RuleResult(4, "size", WARN,
                          f"size check: inputs not recorded for {entries - exact} of {entries} SENT; every SENT's"
                          " shares equal its decision's intent and stay inside the bridge's ceilings and below its"
                          f" identity row at the logged net liquidation{rest}", unchecked + details)
    if unchecked:
        return RuleResult(4, "size", WARN, f"{entries} SENT sized exactly; {len(unchecked)} check(s) could not run",
                          unchecked + details)
    return RuleResult(4, "size", PASS,
                      f"{entries} SENT = floor(policy quantity x identity row) from recorded inputs", details)


def _rule_daily_loss(context: _Context) -> RuleResult:
    timeline = context.timeline
    realized_state = sum((bracket.realized_pnl for bracket in context.state.values()), Decimal("0"))
    session_end = next((run.session_end_pnl for run in reversed(context.section.runs)
                        if run.session_end_pnl is not None), None)
    details = [f"realized P&L: state {realized_state}, EXIT lines {timeline.realized}, session end"
               f" {session_end if session_end is not None else 'not logged'}"]
    failures: list[str] = []
    if session_end is not None and session_end != realized_state:
        details.append("the session-end line and the state differ: the state is used")
    failures += [f"{entry.name} sent after the realized loss had reached {MAX_DAILY_LOSS_PCT}% of net"
                 f" liquidation: {entry.line.evidence()}" for entry in timeline.loss_violations]
    logged = context.net_liquidations
    blocked = [skip for skip in timeline.skips if "daily loss limit" in skip.reason]
    if blocked:
        details.append(f"{len(blocked)} SKIP line(s) for the daily loss limit, first {blocked[0].line.evidence()}")
    if not logged:
        if failures:
            return RuleResult(5, "daily loss", FAIL, failures[0], failures + details)
        status = PASS if not timeline.entries else WARN
        return RuleResult(5, "daily loss", status, f"realized P&L {realized_state}; net liquidation never logged"
                          + ("" if not timeline.entries else ": the -2% stop cannot be checked"), details)
    reference = logged[-1]
    limit = _loss_limit(reference)
    summary = (f"realized P&L {realized_state} = {_percent(realized_state, reference)} of net liquidation"
               f" {reference} (stop at -{MAX_DAILY_LOSS_PCT}% = -{limit.quantize(Decimal('0.01'))})")
    if failures:
        return RuleResult(5, "daily loss", FAIL, f"{len(failures)} problem(s); first: {failures[0]}", failures + details)
    if -realized_state >= limit:
        crossed = timeline.loss_crossed.evidence() if timeline.loss_crossed else "after the last EXIT"
        details.append(f"the loss reached the stop at {crossed}; no entry was sent after it")
        return RuleResult(5, "daily loss", PASS, summary + "; crossed, and no entry after it", details)
    return RuleResult(5, "daily loss", PASS, summary, details)


def _rule_fences(context: _Context) -> RuleResult:
    failures: list[str] = []
    warnings: list[str] = []
    details: list[str] = []
    session_end = context.closes_at
    session_start = datetime.combine(context.day, time(0), tzinfo=NY)
    for fence in context.fences:
        text = (f"{fence.label}: {'STOPPED' if fence.stopped else 'running'} since {fence.updated_text}"
                f" ({fence.reason})")
        details.append(f"fence row {text}")
        relevant = fence.name == GLOBAL_FENCE or fence.identity is not None
        if not relevant:
            continue
        updated = fence.updated_at
        if fence.stopped and (updated is None or updated <= session_end):
            warnings.append(f"fenced during the day: {text}")
            for entry in context.timeline.entries:
                if fence.identity not in (None, entry.identity):
                    continue
                sent_at = entry.sent_at(context.day)
                if sent_at is not None and (updated is None or sent_at >= updated):
                    failures.append(f"{entry.name} sent at {sent_at:%H:%M:%S} ET while {fence.label} was stopped"
                                    f" (since {fence.updated_text})")
        elif not fence.stopped and updated is not None and session_start <= updated <= session_end:
            warnings.append(f"resumed during the day (it was fenced before {updated.astimezone(NY):%H:%M} ET): {text}")
    for run in context.section.runs:
        for line in run.fenced:
            found = _FENCED.match(line.text)
            names = found.group("names").strip() if found else "?"
            if names != "none":
                warnings.append(f"the bridge started with fenced identities: {line.evidence()}")
    for skip in context.timeline.skips:
        if skip.reason.startswith("identity_stopped:") or "kill switch active" in skip.reason:
            warnings.append(f"an entry was refused by a fence: {skip.line.evidence()}")
    connects = sum(len(run.fenced) for run in context.section.runs)
    if failures:
        return RuleResult(6, "fences", FAIL, f"{len(failures)} problem(s); first: {failures[0]}",
                          failures + warnings + details)
    if warnings:
        return RuleResult(6, "fences", WARN, f"{len(warnings)} flag(s); first: {warnings[0]}", warnings + details)
    rows = f"{len(context.fences)} fence row(s), none stopped during the day" if context.fences else "no fence rows"
    return RuleResult(6, "fences", PASS, f"{rows}; {connects} connect(s) logged fenced identities = none", details)


def _inputs(context: _Context, logs: Path, data: Path, db: Path, supervisor_files: int) -> list[str]:
    section = context.section
    first, last = section.runs[0], section.runs[-1]
    versions = sorted({run.version for run in section.runs if run.version})
    shas = sorted({run.sha256[:12] for run in section.runs if run.sha256})
    logged = context.net_liquidations
    sent = sum(1 for bracket in context.state.values() if bracket.sent)
    return [
        f"bridge.log section {context.day}: {len(section.runs)} bridge process(es),"
        f" {first.source}:{first.first_line} to {last.source}:{last.lines[-1].number if last.lines else last.first_line}"
        f" ({', '.join(section.files)} in {logs})",
        f"bridge {', '.join(versions) or 'version not logged'}, sha256 {', '.join(shas) or 'not logged'};"
        f" net liquidation logged {', '.join(str(value) for value in logged) or 'never'}",
        f"supervisor.log: {len(context.supervisor)} bridge event(s) on {context.day} in {supervisor_files} file(s),"
        " rotation-carried lines skipped",
        f"bridge state {BRIDGE_STATE_DIRECTORY}/{context.day}.json: {len(context.state)} bracket(s), {sent} sent"
        f" ({data})",
        f"database {db} (read-only, mode=ro): {len(context.decisions)} shadow_accepted decision(s) on {context.day},"
        f" {len(context.fences)} fence row(s)",
        f"XNYS close {context.closes_at:%H:%M} ET{' (half day)' if context.closes_at.time() != time(16) else ''}",
    ]


def check_session(day: date, *, logs: Path, data: Path, db: Path) -> SessionReport:
    """The six rules for ``day``; nothing read is ever written."""
    report = SessionReport(session_date=day)
    session = market_session(day)
    if session is None:
        report.missing.append(f"{day} is not an XNYS session")
        return report
    try:
        section = read_bridge_section(logs, day)
        supervisor, supervisor_files = read_supervisor_bridge(logs, day)
        state = read_state(data, day)
        with closing(open_read_only(db)) as connection:
            try:
                decisions = read_decisions(connection, day)
                fences = read_fences(connection)
            except sqlite3.Error as exc:
                raise InputsMissing(f"database {db} lacks what the bridge reads ({type(exc).__name__}: {exc})") from exc
            context = _Context(
                day=day, closes_at=session.closes_at.astimezone(NY), section=section, supervisor=supervisor,
                state=state, decisions=decisions, fences=fences,
                timeline=replay(section, state, decisions), connection=connection,
            )
            report.rules = [
                _rule_identities(context), _rule_attribution(context), _rule_caps(context),
                _rule_size(context), _rule_daily_loss(context), _rule_fences(context),
            ]
            report.inputs = _inputs(context, logs, data, db, supervisor_files)
    except InputsMissing as exc:
        report.missing.append(str(exc))
        report.rules = []
    except OSError as exc:  # a log, the state or the database could not be read
        report.missing.append(f"an input is unreadable ({type(exc).__name__}: {exc})")
        report.rules = []
    return report


__all__ = [
    "BRIDGE_IDENTITY_CAPS",
    "DECISIONS_SQL",
    "EXIT_FAILED",
    "EXIT_INPUTS_MISSING",
    "EXIT_PASSED",
    "FAIL",
    "IdentityCaps",
    "InputsMissing",
    "PASS",
    "SessionReport",
    "WARN",
    "check_session",
    "entry_deadline",
    "mask_accounts",
    "open_read_only",
    "parse_intent",
    "read_bridge_section",
    "strategy_name_from_spec",
]
