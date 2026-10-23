"""The session checker on the no-limit PAPER test L-T1 (owner plan v2 of 2026-10-23; plan rev 1 T1-3/T1-4).

Each fixture day is written the way bridge v7c-t1 writes it: its connect lines (the uncapped caps rows,
the measured day risk budget line, the ``t1_mode`` line), ``v7c_budget_ok`` or ``t1_over_v7c_limits``
right before every SENT, IBKR's order statuses, ``t1_stop_repair`` / ``t1_broker_rejected`` /
``t1_account_values`` lines, guard SKIP lines, the bridge state from the bridge's own ``Bracket``, a
real migrated ledger with the AI's verdicts, ``notifier.log`` with the Sentinel's log-only lines and the
research recorder's bars. The line shapes are builder B's (bridge v7c-t1, test_paper_bridge_t1.py); the
tests marked ``needs_b`` feed the merged bridge's own lines through the checker and pin every token,
and the ones marked ``needs_a`` use builder A's test risk policy. Every account id is fake.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import inspect
import json
import os
from pathlib import Path
import time as time_module
from types import MappingProxyType
from typing import Any

import pytest

from tests.test_session_check import (
    BRIDGE_SHA,
    FAKE_ACCOUNT,
    NET_LIQ,
    SETUP,
    V7C_BUDGET_LINE,
    V7C_CAPS_LINES,
    _four,
    _line,
    budget_ok_line,
    pb,
)
from trading.operations import session_check as sc

try:
    from trading.risk.no_limit_test import NO_LIMIT_TEST_POLICY
except ModuleNotFoundError:  # before builder A's T1-1 is merged
    NO_LIMIT_TEST_POLICY = None

needs_a = pytest.mark.skipif(NO_LIMIT_TEST_POLICY is None,
                             reason="needs builder A's trading/risk/no_limit_test.py (L-T1 T1-1, NO_LIMIT_TEST_POLICY)")
B_MERGED = getattr(pb, "NO_LIMIT_TEST", None) is not None
needs_b = pytest.mark.skipif(not B_MERGED,
                             reason="needs builder B's bridge v7c-t1 (scripts_local/paper_bridge.py, L-T1 T1-2)")

MONDAY = date(2026, 11, 2)  # the first test session (Israel = ET+7 from this day)
HALF_DAY = date(2026, 11, 27)  # the 13:00 close (owner decision D6 changed: trade it normally)
# Bridge v7c-t1's connect lines, verbatim (test_paper_bridge.T1_CAPS_LINES, T1_BUDGET_LINE, T1_MODE_LINE;
# pinned to the merged bridge below).
T1_CAPS_LINES = (
    "sendable identities = GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2",
    "caps GAP_GO_V2 positions=none entries/day=none last_entry=close-30m",
    "caps MOMO_HOD_V2 positions=none entries/day=none last_entry=close-60m",
    "caps ORB_V2 positions=none entries/day=none last_entry=close-30m",
    "caps VWAP_PB_V2 positions=none entries/day=none last_entry=close-30m",
)
T1_BUDGET_LINE = ("day risk budget 3% of net liquidation, account level (realized loss + open risk to current stops"
                  " + new risk, measured and logged, never blocks bot entries (no-limit test L-T1), owner buys still"
                  " refused over it; stop slippage factor 1.0)")
T1_MODE_LINE = ("t1_mode no-limit PAPER test L-T1 on: no position, entries/day or account-wide caps; the -2% daily"
                " loss stop and the 3% risk budget are measured and logged, never block bot entries (owner buys keep"
                " both); robot stops repair themselves (60s, then close at market); guards: 20 entries per identity"
                " per 60s, 3 broker rejections halt an identity for the day, 3 entries per symbol per day, duplicates"
                " within 5s, no entry outside the session")
BUYING_POWER_REJECTION = "201 Order rejected - reason: insufficient buying power"


# --- the fixture day -------------------------------------------------------------------------------------


def ny(day: date, clock: str, seconds: float = 0) -> datetime:
    return datetime.combine(day, time.fromisoformat(clock), tzinfo=sc.NY) + timedelta(seconds=seconds)


def hms(day: date, clock: str, seconds: float = 0) -> str:
    return ny(day, clock, seconds).strftime("%H:%M:%S")


@dataclass(frozen=True)
class Signal:
    """One bot entry: its SENT, what IBKR did with it, and its exit (None: still held)."""

    identity: str
    symbol: str
    qty: int
    entry: str
    stop: str
    target: str
    sent: str
    closed: str | None
    exit_price: str | None
    exit_reason: str = "target"
    measured: str = "ok"  # "ok" (v7c_budget_ok), "over:<tokens>" (t1_over_v7c_limits) or "none"
    open_risk: str | None = None  # the measurement's open risk instead of the replayed one
    rejected: str | None = None  # "<TWS code> <message>": IBKR rejected the entry
    decided_before: float = 2  # the decision's age when its order was placed (s)
    ack_after: int = 1  # IBKR's first status this long after the SENT (s)

    @property
    def pnl(self) -> Decimal:
        if self.exit_price is None:
            return Decimal("0.00")
        return ((Decimal(self.exit_price) - Decimal(self.entry)) * self.qty).quantize(Decimal("0.01"))

    @property
    def risk(self) -> Decimal:
        return self.qty * (Decimal(self.entry) - Decimal(self.stop))

    @property
    def decision_id(self) -> str:
        return f"t1-{self.identity}-{self.symbol}-{self.sent.replace(':', '')}"


@dataclass(frozen=True)
class Other:
    """A decision the bridge never sends: the AI's ABSTAIN, a risk block, a rule rejection."""

    identity: str
    symbol: str
    clock: str
    outcome: str  # ai_abstained | ai_blocked | risk_blocked | rule_rejected
    reason: str = ""


@dataclass(frozen=True)
class Skipped:
    """An accepted decision the bridge skipped (its SKIP line and state bracket)."""

    identity: str
    symbol: str
    clock: str
    reason: str


@dataclass
class T1Day:
    day: date = MONDAY
    signals: tuple[Signal, ...] = ()
    others: tuple[Other, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    extra: tuple[tuple[str, str, str], ...] = ()
    net_liq: str = NET_LIQ
    caps_lines: tuple[str, ...] = T1_CAPS_LINES
    budget_line: str = T1_BUDGET_LINE
    mode_line: bool = True
    policy: str = "test"  # test | default | other
    recorded_inputs: bool = False
    notifier: tuple[str, ...] = ()
    research: tuple[tuple[int, str, tuple[tuple[str, str, str, str, str, str], ...], str | None], ...] = ()
    second_process: str | None = None  # "v7c": a second bridge process at 12:00 that ran v7c


@dataclass
class Paths:
    logs: Path
    data: Path
    db: Path


def session_close(day: date) -> datetime:
    session = sc.market_session(day)
    assert session is not None
    return session.closes_at.astimezone(sc.NY)


def over_line(symbol: str, label: str, *, realized: Decimal, open_risk: Decimal, new: Decimal, net_liq: str,
              tokens: str) -> str:
    """Bridge v7c-t1's t1_over_v7c_limits text (process_decision), from the bridge's own Budget."""
    budget = pb.Budget(realized_loss=realized, open_risk=open_risk, new_risk=new, net_liq=Decimal(net_liq))
    return (f"t1_over_v7c_limits {symbol} ({label}): {budget.text()} vs {pb.DAY_RISK_BUDGET_PCT}% of"
            f" {pb.money(budget.net_liq)} — would refuse: {tokens} (source=bot)")


def measurement(spec: T1Day, signal: Signal) -> str | None:
    """The signal's measurement line: the open risk of the entries still held, the realized loss so far."""
    if signal.measured == "none":
        return None
    sent = time.fromisoformat(signal.sent)
    earlier = [other for other in spec.signals if time.fromisoformat(other.sent) < sent and other.rejected is None]
    held = [other for other in earlier if other.closed is None or time.fromisoformat(other.closed) > sent]
    open_risk = sum((other.risk for other in held), Decimal("0"))
    if signal.open_risk is not None:
        open_risk = Decimal(signal.open_risk)
    realized = sum((other.pnl for other in earlier if other.closed is not None
                    and time.fromisoformat(other.closed) <= sent), Decimal("0"))
    numbers = {"realized": max(Decimal("0"), -realized), "open_risk": open_risk, "new": signal.risk,
               "net_liq": spec.net_liq}
    if signal.measured == "ok":
        return budget_ok_line(signal.symbol, signal.identity, **numbers)
    return over_line(signal.symbol, signal.identity, tokens=signal.measured.split(":", 1)[1], **numbers)


def events(spec: T1Day) -> list[tuple[str, str, str]]:
    day = spec.day
    close = session_close(day)
    flatten = close - timedelta(minutes=15)
    rows: list[tuple[str, str, str]] = []
    rejected_once: set[str] = set()
    for index, signal in enumerate(spec.signals):
        parent = 100 + 3 * index
        line = measurement(spec, signal)
        if line is not None:
            rows.append((signal.sent, "INFO", line))
        rows.append((signal.sent, "SENT", f"{signal.symbol} ({signal.identity}): BUY {signal.qty} @ {signal.entry} |"
                     f" stop {signal.stop} | target {signal.target} (orders {parent}-{parent + 2})"))
        if signal.rejected is not None:
            code, message = signal.rejected.split(" ", 1)
            first = signal.identity not in rejected_once
            rejected_once.add(signal.identity)
            rows += [
                (hms(day, signal.sent, 1), "INFO", f"TWS {parent} {code} {message} (entry order: the bridge pages it"
                 " once)"),
                (hms(day, signal.sent, 1), "INFO", f"order {parent}: Inactive filled=0.0 remaining={signal.qty}.0"
                 " avg=0.0"),
                (hms(day, signal.sent, 2), "INFO", f"{signal.symbol}: entry never filled — rejected by IBKR"),
                (hms(day, signal.sent, 2), "CRITICAL" if first else "INFO",
                 f"t1_broker_rejected {signal.symbol} ({signal.identity}): IBKR rejected the entry (TWS {code}"
                 f" {message}) — not sent again; 1 in a row for {signal.identity} (halts at 3)"
                 + ("; later rejections today are logged, not paged" if first else "")),
            ]
            continue
        rows += [
            (hms(day, signal.sent, signal.ack_after), "INFO",
             f"order {parent}: Submitted filled=0.0 remaining={signal.qty}.0 avg=0.0"),
            (hms(day, signal.sent, 2), "FILL", f"{signal.symbol}: {signal.qty}/{signal.qty} sh @ {_four(signal.entry)}"
             f" ({signal.identity})"),
        ]
        if signal.closed is None or signal.exit_price is None:
            continue
        if signal.exit_reason == "flatten":
            rows.append(((flatten + timedelta(seconds=1)).strftime("%H:%M:%S"), "FLATTEN",
                         f"{signal.symbol}: target repriced to {signal.exit_price} for {signal.qty} sh"
                         f" (bid {signal.exit_price})"))
        rows += [
            (signal.closed, "EXIT", f"{signal.symbol}: {signal.qty} sh @ {_four(signal.exit_price)} via"
             f" {signal.exit_reason} | P&L {signal.pnl}"),
            (signal.closed, "CLOSED", f"{signal.symbol}: P&L {signal.pnl} ({signal.exit_reason})"),
        ]
    rows += [(item.clock, "SKIP", f"{item.symbol} ({item.identity}): {item.reason}") for item in spec.skipped]
    rows.append((flatten.strftime("%H:%M:%S"), "INFO", f"{flatten:%H:%M} ET: flattening all engine positions"))
    rows.append(((close - timedelta(minutes=10)).strftime("%H:%M:%S"), "FLAT", "no open engine positions"))
    rows += list(spec.extra)
    return sorted(rows, key=lambda row: row[0])


def connect_lines(spec: T1Day, clock: str, caps_lines: tuple[str, ...], *, budget_line: str | None = None,
                  mode_line: bool | None = None) -> list[str]:
    stop = session_close(spec.day) - timedelta(minutes=5)
    version = "v7c-t1" if (spec.mode_line if mode_line is None else mode_line) else "v7c"
    lines = [
        _line(clock, "INFO", f"PAPER bridge {version} connected (all accounts paper). Managing brackets for"
              f" {spec.day} until {stop:%H:%M} ET."),
        *(_line(clock, "INFO", text) for text in caps_lines),
        _line(clock, "INFO", "fenced identities = none"),
        _line(clock, "INFO", budget_line or spec.budget_line),
        *([_line(clock, "INFO", T1_MODE_LINE)] if (spec.mode_line if mode_line is None else mode_line) else []),
        _line(clock, "INFO", f"state /home/test/.local/share/autotrader-v1/bridge/{spec.day}.json: 0 brackets,"
              " 0 active"),
        _line(clock, "INFO", f"net liquidation {spec.net_liq}; daily loss stop 2% (bot entries: measured only,"
              " no-limit test L-T1)"),
    ]
    return lines


def write_logs(logs: Path, spec: T1Day) -> None:
    logs.mkdir(parents=True)
    day, close = spec.day, session_close(spec.day)
    start = ny(day, "09:25:00").astimezone(timezone.utc)
    lines = [
        f"=== {start.isoformat()} supervisor starts bridge: /repo/backend/.venv/bin/python"
        f" /repo/backend/scripts_local/paper_bridge.py --date {day}",
        _line("09:25:00", "INFO", f"bridge sha256={BRIDGE_SHA}"),
        *connect_lines(spec, "09:25:00", spec.caps_lines),
    ]
    body = [_line(clock, kind, text) for clock, kind, text in events(spec)]
    if spec.second_process == "v7c":  # a second bridge process (its own sha line) that ran v7c
        cut = next((index for index, text in enumerate(body) if text[:8] >= "12:00:00"), len(body))
        body[cut:cut] = [_line("12:00:00", "INFO", f"bridge sha256={BRIDGE_SHA}"),
                         *connect_lines(spec, "12:00:00", V7C_CAPS_LINES, budget_line=V7C_BUDGET_LINE,
                                        mode_line=False)]
    lines += body
    realized = sum((signal.pnl for signal in spec.signals), Decimal("0"))
    end = (close - timedelta(minutes=5)).strftime("%H:%M:%S")
    lines += [_line(end, "INFO", f"session end: realized P&L {realized}"), _line(end, "INFO", "bridge stopped (exit 0)")]
    (logs / "bridge.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    final = (close - timedelta(minutes=5)).astimezone(timezone.utc)
    (logs / "supervisor.log").write_text(
        f"{start.isoformat()} CHILD_START bridge pid=301\n{final.isoformat()} CHILD_EXIT bridge code=0"
        " restart_in=none\n", encoding="utf-8")
    if spec.notifier:
        (logs / "notifier.log").write_text("\n".join(spec.notifier) + "\n", encoding="utf-8")


def con_id(index: int) -> int:
    return 3000 + index


def write_state(data: Path, spec: T1Day) -> None:
    brackets: dict[str, Any] = {}
    for index, signal in enumerate(spec.signals):
        parent = 100 + 3 * index
        held = signal.rejected is None
        closed = held and signal.closed is not None
        bracket = pb.Bracket(
            decision_id=signal.decision_id, symbol=signal.symbol, con_id=con_id(index), primary_exchange="NASDAQ",
            strategy=pb.strategy_family(signal.identity), qty=signal.qty, entry=signal.entry, stop=signal.stop,
            target=signal.target, parent=parent, target_id=parent + 1, stop_id=parent + 2,
            sent_at=ny(spec.day, signal.sent).astimezone(timezone.utc).isoformat(),
            status="closed" if closed else ("open" if held else "cancelled"), filled=signal.qty if held else 0,
            avg_entry=_four(signal.entry) if held else "0", exit_filled=signal.qty if closed else 0,
            avg_exit=_four(signal.exit_price) if closed and signal.exit_price else "0",
            exit_reason=signal.exit_reason if closed else "", realized_pnl=str(signal.pnl) if closed else "0",
            strategy_name=signal.identity, run_id=run_id(signal.identity, spec.day),
            flatten_attempted=signal.exit_reason == "flatten", flatten_sent=signal.exit_reason == "flatten",
        )
        brackets[signal.decision_id] = {**pb.asdict(bracket),
                                        **({"broker_rejection": signal.rejected} if signal.rejected else {})}
    for item in spec.skipped:
        bracket = pb.Bracket(
            decision_id=skipped_id(item), symbol=item.symbol, con_id=2000, primary_exchange="NYSE",
            strategy=pb.strategy_family(item.identity), qty=100, entry="10.00", stop="9.90", target="10.20",
            skipped=True, status="skipped", skip_reason=item.reason, strategy_name=item.identity,
            run_id=run_id(item.identity, spec.day),
        )
        brackets[bracket.decision_id] = pb.asdict(bracket)
    directory = data / "bridge"
    directory.mkdir(parents=True)
    document = {"schema": pb.STATE_SCHEMA, "session_date": spec.day.isoformat(), "submitted": brackets}
    (directory / f"{spec.day}.json").write_text(json.dumps(document, indent=2), encoding="utf-8")


def write_research(data: Path, spec: T1Day) -> None:
    from trading.research.bars_store import BarsStore, ResearchPaths, StoredBar, StoredDay

    store = BarsStore(ResearchPaths(root=data / "research"))
    for contract, symbol, bars, prior in spec.research:
        store.write_day(StoredDay(
            session_date=spec.day, con_id=contract, symbol=symbol, prior_close=None if prior is None else Decimal(prior),
            bars=tuple(StoredBar(interval_start_utc=ny(spec.day, start).astimezone(timezone.utc), open=Decimal(o),
                                 high=Decimal(h), low=Decimal(low), close=Decimal(c), volume=Decimal(v))
                       for start, o, h, low, c, v in bars),
        ))


def run_id(identity: str, day: date) -> str:
    return f"t1-{identity.lower()}-{day}"


def skipped_id(item: Skipped) -> str:
    return f"t1-skip-{item.identity}-{item.symbol}"


def policy_of(spec: T1Day) -> Any:
    from trading.contracts import RiskPolicy

    if spec.policy == "test" and NO_LIMIT_TEST_POLICY is not None:
        return NO_LIMIT_TEST_POLICY
    if spec.policy == "other":
        return RiskPolicy(id="unreviewed-policy", version=7)
    return RiskPolicy()


async def seed_ledger(db: Path, monkeypatch: pytest.MonkeyPatch, spec: T1Day) -> None:
    from config import cfg
    from db.core import init_db
    from tests.fakes.legacy_strategies import insert_legacy_shadow_strategy
    from trading.contracts import (
        AccountSnapshot, AIReview, Candidate, Decision, DecisionOutcome, EngineMode, FixedUniverse, RiskApproval,
        RuleCondition, Run, ScannerRun, StrategySpec, TradeIntent, fingerprint_payload,
    )
    from trading.data import CandidateRepository, RunRepository, ScannerRunRepository
    from trading.risk.portfolio import portfolio_from_account
    from trading.storage.repository import record_decision
    from trading.storage.shadow_ledger import SQLiteShadowLedger

    monkeypatch.setattr(cfg, "DB_PATH", str(db))
    await init_db()
    policy = policy_of(spec)
    day = spec.day
    rows: list[tuple[str, str, str, str, Signal | None, str, str]] = [
        (signal.identity, signal.symbol, ny(day, signal.sent, -signal.decided_before).isoformat(), "shadow_accepted",
         signal, signal.decision_id, "") for signal in spec.signals
    ] + [(item.identity, item.symbol, ny(day, item.clock, -2).isoformat(), "shadow_accepted", None, skipped_id(item),
          "") for item in spec.skipped] + [
        (item.identity, item.symbol, ny(day, item.clock).isoformat(), item.outcome, None,
         f"t1-other-{item.identity}-{item.symbol}", item.reason) for item in spec.others
    ]
    strategies: dict[str, str] = {}
    pending: dict[str, list[str]] = {}
    ranks: dict[str, int] = {}
    for number, (name, symbol, created_text, outcome, signal, decision_id, reason) in enumerate(rows):
        if name not in strategies:
            spec_model = StrategySpec(
                name=name, universe=FixedUniverse(symbols=("AAPL",)),
                conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
                rationale=f"{name} no-limit test fixture.",
            )
            strategies[name] = (await insert_legacy_shadow_strategy(spec=spec_model, owner_id="demo")).strategy_id
            await RunRepository().save(Run(
                id=run_id(name, day), owner_id="demo", source_sha="abcdef1", strategy_id=strategies[name],
                strategy_version=1, policy_fingerprints={"risk": policy.fingerprint()}, session_date=day,
                mode=EngineMode.SHADOW,
            ))
            parameters = {"scan_code": "HOT_BY_VOLUME", "limit": 50}
            await ScannerRunRepository().save(ScannerRun(
                id=f"{run_id(name, day)}-scan", run_id=run_id(name, day), parameters=parameters,
                parameters_fingerprint=fingerprint_payload(parameters), status="completed",
                started_at=ny(day, "09:00:00").astimezone(timezone.utc),
            ))
        run = run_id(name, day)
        ranks[run] = ranks.get(run, 0) + 1
        candidate = Candidate(id=f"{run}-{symbol}-{number}", scanner_run_id=f"{run}-scan", con_id=5000 + number,
                              symbol=symbol, primary_exchange="NASDAQ", rank=ranks[run], admission_status="admitted")
        await CandidateRepository().save(candidate)
        created = datetime.fromisoformat(created_text).astimezone(timezone.utc)
        key = sha256(decision_id.encode()).hexdigest()
        common = {"id": decision_id, "run_id": run, "candidate_id": candidate.id, "strategy_id": strategies[name],
                  "strategy_version": 1, "decision_key": key, "created_at": created}
        if outcome == "rule_rejected":
            await record_decision(decision=Decision(outcome=DecisionOutcome.RULE_REJECTED, **common), owner_id="demo")
            continue
        verdict = "ABSTAIN" if outcome in {"ai_abstained", "ai_blocked"} else "PROPOSE"
        review = AIReview(
            setup_fingerprint=SETUP, decision=verdict, confidence="0.7",
            rationale=reason if outcome == "ai_abstained" else "Fixture.",
            model_id="fixture-model", prompt_version="fixture-prompt", response_hash="c" * 64, latency_ms=5,
            cost_usd="0" if outcome == "ai_blocked" else "0.01",
            failure_reason=reason if outcome == "ai_blocked" else None,
        )
        if outcome in {"ai_abstained", "ai_blocked"}:
            await record_decision(decision=Decision(
                outcome=DecisionOutcome(outcome), setup_fingerprint=SETUP, ai_review=review, **common), owner_id="demo")
            continue
        expires = created + timedelta(seconds=15)
        portfolio_fingerprint = "e" * 64
        if spec.recorded_inputs:
            account = AccountSnapshot(
                paper_account_id="DU1234567", client_id=19, observed_at=created - timedelta(seconds=1),
                net_liquidation=Decimal(NET_LIQ), available_funds=Decimal(NET_LIQ),
                realized_day_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
            )
            await SQLiteShadowLedger().save_account_snapshot(account, run_id=run)
            portfolio_fingerprint = portfolio_from_account(
                account, owned_con_ids=frozenset(), pending_symbols=tuple(sorted(pending.get(run, [])))).fingerprint
        pending.setdefault(run, []).append(symbol)
        if outcome == "risk_blocked":
            risk = RiskApproval(approved=False, codes=("size_scaled_to_zero",), setup_fingerprint=SETUP,
                                ai_review_fingerprint=review.fingerprint(), policy_fingerprint=policy.fingerprint(),
                                portfolio_fingerprint=portfolio_fingerprint, expires_at=expires)
            await record_decision(decision=Decision(outcome=DecisionOutcome.RISK_BLOCKED, setup_fingerprint=SETUP,
                                                    ai_review=review, risk=risk, **common), owner_id="demo")
            continue
        quantity, entry, stop, target = ((signal.qty, signal.entry, signal.stop, signal.target) if signal
                                         else (100, "10.00", "9.90", "10.20"))
        intent = TradeIntent(
            execution_key=f"exec-{decision_id}", setup_fingerprint=SETUP, approval_token="t" * 32, expires_at=expires,
            strategy_id=strategies[name], strategy_version=1, symbol=symbol, quantity=quantity, entry_limit=entry,
            stop_price=stop, target_price=target,
        )
        await record_decision(decision=Decision(
            outcome=DecisionOutcome.SHADOW_ACCEPTED, setup_fingerprint=SETUP, ai_review=review,
            risk=RiskApproval(approved=True, intent=intent, setup_fingerprint=SETUP,
                              ai_review_fingerprint=review.fingerprint(), policy_fingerprint=policy.fingerprint(),
                              portfolio_fingerprint=portfolio_fingerprint, expires_at=expires),
            **common), owner_id="demo")


async def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: T1Day) -> Paths:
    paths = Paths(logs=tmp_path / "logs", data=tmp_path / "data", db=tmp_path / "data" / "autotrader.db")
    write_logs(paths.logs, spec)
    write_state(paths.data, spec)
    write_research(paths.data, spec)
    await seed_ledger(paths.db, monkeypatch, spec)
    return paths


def check(paths: Paths, day: date = MONDAY) -> sc.SessionReport:
    return sc.check_session(day, logs=paths.logs, data=paths.data, db=paths.db)


def rule(report: sc.SessionReport, number: int) -> sc.RuleResult:
    return next(item for item in report.rules if item.number == number)


def statuses(report: sc.SessionReport) -> dict[int, str]:
    return {item.number: item.status for item in report.rules}


def text_of(result: sc.RuleResult) -> str:
    return "\n".join([result.summary, *result.details])


@contextmanager
def local_clock(zone: str) -> Iterator[None]:
    """The process's own time zone (the Mac's clock), as the owner's Mac in Israel runs."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = zone
    time_module.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time_module.tzset()


# Sized at net liquidation 100000 so that every row of either identity table passes rule 4's bound
# (v7c: ORB x0.25, VWAP x0.5; the test: x1 per Step 0).
def momo(symbol: str, sent: str, closed: str | None, exit_price: str | None, reason: str = "target",
         **kwargs: Any) -> Signal:
    return Signal("MOMO_HOD_V2", symbol, 750, "20.00", "19.80", "20.40", sent, closed, exit_price, reason, **kwargs)


def orb(symbol: str, sent: str, closed: str | None, exit_price: str | None, reason: str = "target",
        **kwargs: Any) -> Signal:
    return Signal("ORB_V2", symbol, 75, "50.00", "49.50", "51.00", sent, closed, exit_price, reason, **kwargs)


def vwap(symbol: str, sent: str, closed: str | None, exit_price: str | None, reason: str = "target",
         **kwargs: Any) -> Signal:
    return Signal("VWAP_PB_V2", symbol, 750, "10.00", "9.90", "10.20", sent, closed, exit_price, reason, **kwargs)


CLEAN = (momo("AAA", "09:45:00", "10:10:00", "20.40"), orb("BBB", "09:50:00", "10:20:00", "49.50", "stop"),
         vwap("CCC", "11:30:00", "15:45:03", "10.02", "flatten"))
ALL_CLEAN = {1: sc.PASS, 2: sc.PASS, 3: sc.PASS, 4: sc.WARN, 5: sc.INFO, 6: sc.PASS, 7: sc.PASS, 8: sc.INFO,
             9: sc.PASS}


# --- the recorded-shape session: rule 8 equals the hand count ----------------------------------------


GUARD_SKIP = ("13:00:00", "SKIP", "ZZZ (MOMO_HOD_V2): t1_guard_duplicate: BUY 750 ZZZ repeats order 109 sent 1.0s ago"
              " (window 5s) — refused")
GUARD_PAGE = ("13:00:00", "CRITICAL", "t1_guard_duplicate ZZZ (MOMO_HOD_V2): BUY 750 ZZZ repeats order 109 sent 1.0s"
              " ago (window 5s) — refused")
REPAIR = (
    ("10:45:00", "CRITICAL", "t1_stop_repair GGG (ORB_V2): stop order 108 is not live (Cancelled) — 75 sh unprotected;"
     " stop resubmitted once as order 200 at 49.50 in the target OCA group; closed at market if it is not working by"
     " 10:46:00 ET; entries are refused meanwhile"),
    ("10:45:02", "INFO", "t1_stop_repair GGG (ORB_V2): repaired — stop order 200 works at 49.50 2s after detection"),
)
ACCOUNT_VALUES = tuple(
    (clock, "INFO", f"t1_account_values NetLiquidation=100000.00 AvailableFunds={funds} BuyingPower={power}"
     f" ExcessLiquidity={funds} Cushion={cushion} GrossPositionValue={gross}")
    for clock, funds, power, cushion, gross in (
        ("09:45:30", "96000.00", "385000.00", "0.96", "15000.00"),
        ("09:56:00", "88000.00", "355000.00", "0.88", "45000.00"),
        ("11:31:00", "96500.00", "385000.00", "0.965", "15000.00"),
    )
)
DAY_SIGNALS = (
    momo("AAA", "09:45:00", "10:10:00", "20.40", decided_before=1, ack_after=1),
    orb("BBB", "09:50:00", "10:20:00", "49.50", "stop", decided_before=2, ack_after=1),
    orb("GGG", "09:55:00", "11:00:00", "51.00", decided_before=3, ack_after=3),
    momo("AAA", "10:30:00", "10:40:00", "19.80", "stop", measured="over:v7c_day_risk_budget", open_risk="2990.00",
         decided_before=4, ack_after=2),
    Signal("MOMO_HOD_V2", "EEE", 500, "30.00", "29.70", "30.60", "11:15:00", None, None,
           rejected=BUYING_POWER_REJECTION),
    vwap("CCC", "11:30:00", "15:45:03", "10.02", "flatten", decided_before=5, ack_after=1),
)
DAY_OTHERS = (
    Other("GAP_GO_V2", "DDD", "10:00:00", "ai_abstained", "Gap faded below VWAP."),
    Other("GAP_GO_V2", "FFF", "10:05:00", "ai_abstained", "Gap faded below VWAP."),
    Other("GAP_GO_V2", "JJJ", "10:10:00", "ai_blocked", "budget_exhausted"),
    Other("MOMO_HOD_V2", "KKK", "10:15:00", "risk_blocked"),
    Other("VWAP_PB_V2", "LLL", "10:20:00", "rule_rejected"),
    Other("MOMO_HOD_V2", "MMM", "10:25:00", "ai_abstained", "Extended above VWAP."),
)
DAY_SKIPPED = (
    Skipped("ORB_V2", "HHH", "12:00:00", "decision expired (65s old)"),
    Skipped("MOMO_HOD_V2", "ZZZ", "13:00:00", GUARD_SKIP[2].split(": ", 1)[1]),
)
# notifier.log: the Sentinel's log-only line today (Israel time, UTC+2), yesterday's, and a rotation's copy.
DAY_NOTIFIER = (
    "2026-10-30T18:00:00+0200 WARNING autotrader.shadow.sentinel sentinel would fence GAP_GO_V2: daily loss 1.30 R"
    " > 1.25 R (realized -$130.00)",
    "2026-11-02T18:30:00+0200 WARNING autotrader.shadow.sentinel sentinel would fence ORB_V2: daily loss 1.30 R >"
    " 1.25 R (realized -$130.00)",
    "2026-11-02T18:30:00+0200 WARNING autotrader.shadow.sentinel sentinel would fence ORB_V2: daily loss 1.30 R >"
    " 1.25 R (realized -$130.00)",
)
SPY_ID, CCC_ID = 756733, con_id(5)
DAY_RESEARCH = (
    (SPY_ID, "SPY", (("09:30:00", "501", "503", "499", "502", "1000"), ("15:55:00", "502", "506", "501", "505", "900")),
     "500.00"),
    (CCC_ID, "CCC", (("09:30:00", "10.00", "10.10", "9.95", "10.05", "1500"),
                     ("09:35:00", "10.05", "10.08", "10.00", "10.02", "1500")), None),
)
RECORDED_DAY = T1Day(signals=DAY_SIGNALS, others=DAY_OTHERS, skipped=DAY_SKIPPED,
                     extra=(GUARD_PAGE, *REPAIR, *ACCOUNT_VALUES), notifier=DAY_NOTIFIER,
                     research=DAY_RESEARCH)


def trade(identity: str, symbol: str, orders: str, sent: str, closed: str, qty: int, entry: str, stop: str,
          exit_price: str, reason: str, pnl: str, r: str, volume: str | None = None) -> dict[str, Any]:
    return {"identity": identity, "symbol": symbol, "orders": orders, "sent": sent, "closed": closed, "qty": qty,
            "filled": qty, "entry": entry, "stop": stop, "avg_entry": _four(entry), "avg_exit": _four(exit_price),
            "exit_reason": reason, "pnl": pnl, "r": r, "pct_day_volume": volume}


def quantiles(p50: Any, p95: Any, top: Any, count: int) -> dict[str, Any]:
    return {"p50": p50, "p95": p95, "max": top, "n": count}


NO_LATENCY = quantiles(None, None, None, 0)
# The hand count of RECORDED_DAY (every number below was counted from the lines above, not computed).
T_AAA1 = trade("MOMO_HOD_V2", "AAA", "100-102", "09:45:00", "10:10:00", 750, "20.00", "19.80", "20.40", "target",
               "300.00", "2.00")
T_BBB = trade("ORB_V2", "BBB", "103-105", "09:50:00", "10:20:00", 75, "50.00", "49.50", "49.50", "stop", "-37.50",
              "-1.00")
T_GGG = trade("ORB_V2", "GGG", "106-108", "09:55:00", "11:00:00", 75, "50.00", "49.50", "51.00", "target", "75.00",
              "2.00")
T_AAA2 = trade("MOMO_HOD_V2", "AAA", "109-111", "10:30:00", "10:40:00", 750, "20.00", "19.80", "19.80", "stop",
               "-150.00", "-1.00")
T_CCC = trade("VWAP_PB_V2", "CCC", "115-117", "11:30:00", "15:45:03", 750, "10.00", "9.90", "10.02", "flatten",
              "15.00", "0.20", "0.25")
EXPECTED = {
    "MOMO_HOD_V2": {
        "signals": 6,
        "ai": {"approved": 5, "rejected": 1, "top_rejection_reasons": [{"reason": "Extended above VWAP.", "count": 1}],
               "cost_usd": "0.06"},
        "decisions": {"accepted": 4, "risk_blocked": 1},
        "orders": {"sent": 3, "filled": 2, "rejected": {"201": 1}, "expired_unsent": 0},
        "positions_at_once": quantiles(1, 1, 1, 3),
        "entries": 3,
        "reentries": {"count": 1, "symbols": {"AAA": 2}},
        "trades": [T_AAA1, T_AAA2],
        "mean_r": "0.50", "win_rate_pct": "50.00", "pnl": "150.00", "max_drawdown": "150.00",
        "latency_s": {"signal_to_ack": quantiles("2", "6", "6", 2), "signal_to_sent": quantiles("2", "4", "4", 3)},
        "guards": {"t1_guard_duplicate": 1},
    },
    "ORB_V2": {
        "signals": 3,
        "ai": {"approved": 3, "rejected": 0, "top_rejection_reasons": [], "cost_usd": "0.03"},
        "decisions": {"accepted": 3, "risk_blocked": 0},
        "orders": {"sent": 2, "filled": 2, "rejected": {}, "expired_unsent": 1},
        "positions_at_once": quantiles(1, 2, 2, 2),
        "entries": 2,
        "reentries": {"count": 0, "symbols": {}},
        "trades": [T_BBB, T_GGG],
        "mean_r": "0.50", "win_rate_pct": "50.00", "pnl": "37.50", "max_drawdown": "37.50",
        "latency_s": {"signal_to_ack": quantiles("3", "6", "6", 2), "signal_to_sent": quantiles("2", "3", "3", 2)},
        "guards": {},
    },
    "VWAP_PB_V2": {
        "signals": 1,
        "ai": {"approved": 1, "rejected": 0, "top_rejection_reasons": [], "cost_usd": "0.01"},
        "decisions": {"accepted": 1, "risk_blocked": 0},
        "orders": {"sent": 1, "filled": 1, "rejected": {}, "expired_unsent": 0},
        "positions_at_once": quantiles(1, 1, 1, 1),
        "entries": 1,
        "reentries": {"count": 0, "symbols": {}},
        "trades": [T_CCC],
        "mean_r": "0.20", "win_rate_pct": "100.00", "pnl": "15.00", "max_drawdown": "0",
        "latency_s": {"signal_to_ack": quantiles("6", "6", "6", 1), "signal_to_sent": quantiles("5", "5", "5", 1)},
        "guards": {},
    },
    "GAP_GO_V2": {
        "signals": 3,
        "ai": {"approved": 0, "rejected": 3,
               "top_rejection_reasons": [{"reason": "Gap faded below VWAP.", "count": 2},
                                         {"reason": "budget_exhausted", "count": 1}],
               "cost_usd": "0.02"},
        "decisions": {"accepted": 0, "risk_blocked": 0},
        "orders": {"sent": 0, "filled": 0, "rejected": {}, "expired_unsent": 0},
        "positions_at_once": quantiles(None, None, None, 0),
        "entries": 0,
        "reentries": {"count": 0, "symbols": {}},
        "trades": [],
        "mean_r": None, "win_rate_pct": None, "pnl": "0", "max_drawdown": "0",
        "latency_s": {"signal_to_ack": NO_LATENCY, "signal_to_sent": NO_LATENCY},
        "guards": {},
    },
}
EXPECTED_TOTAL = {
    "signals": 13,
    "ai": {"approved": 9, "rejected": 4,
           "top_rejection_reasons": [{"reason": "Gap faded below VWAP.", "count": 2},
                                     {"reason": "Extended above VWAP.", "count": 1},
                                     {"reason": "budget_exhausted", "count": 1}],
           "cost_usd": "0.12"},
    "decisions": {"accepted": 8, "risk_blocked": 1},
    "orders": {"sent": 6, "filled": 5, "rejected": {"201": 1}, "expired_unsent": 1},
    # Open at each SENT, all identities: AAA 1; BBB 2; GGG 3; AAA again 2 (GGG still open); EEE 1; CCC 1.
    "positions_at_once": quantiles(1, 3, 3, 6),
    "entries": 6,
    "reentries": {"count": 1, "symbols": {"AAA": 2}},
    "trades": [T_AAA1, T_BBB, T_GGG, T_AAA2, T_CCC],
    # R: 2, -1, 2, -1, 0.2 -> 0.44; wins 3 of 5; P&L 300 - 37.50 + 75 - 150 + 15; the curve by close time
    # 300, 262.50, 112.50, 187.50, 202.50 falls 187.50 from its 300 peak.
    "mean_r": "0.44", "win_rate_pct": "60.00", "pnl": "202.50", "max_drawdown": "187.50",
    "latency_s": {"signal_to_ack": quantiles("6", "6", "6", 5), "signal_to_sent": quantiles("2", "5", "5", 6)},
    "guards": {"t1_guard_duplicate": 1},
}
EXPECTED_ACCOUNT = {
    "buying_power": {"samples": 3, "peak_used_pct": "11.25", "min_available_funds": "88000.00", "min_cushion": "0.88",
                     "definition": "GrossPositionValue / (GrossPositionValue + BuyingPower)"},
    "close_out": {"last_flat": "15:45:03", "limit": "15:50", "open_at_end": []},
    "stop_repairs": {"episodes": 1, "repaired": 1, "closed_at_market": 0, "unresolved": 0, "longest_s": 2},
    "guards": {"t1_guard_duplicate": 1},
    "sentinel_would_fence": {"status": "read", "identities": {"ORB_V2": 1}},
    "loss_step_pages": [],
    "low_cushion_pages": 0,
    "manual_unprotected_pages": 0,
    "market": {
        "SPY": {"status": "read", "return_pct": "1.00", "range_pct": "1.40", "base": "prior close"},
        "QQQ": {"status": "n/a", "reason": "no research bars for QQQ on 2026-11-02 (the recorder runs 16:20-17:30 ET"
                                           " and stores the day's contracts)"},
    },
    "size_vs_displayed": {"status": "n/a", "reason": "the bridge logs no displayed (bid or ask) size at the quote; %"
                                                     " of the day's volume is per trade"},
}


async def test_rule_8_on_a_recorded_session_equals_the_hand_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = check(await build(tmp_path, monkeypatch, RECORDED_DAY))

    assert statuses(report) == ALL_CLEAN, report.render()
    assert report.mode == sc.NO_LIMIT_MODE and report.metrics is not None
    metrics = report.metrics.as_json()
    for name, expected in EXPECTED.items():
        assert metrics["strategies"][name] == expected, name
    assert metrics["total"] == EXPECTED_TOTAL
    assert metrics["account"] == EXPECTED_ACCOUNT
    result = rule(report, sc.METRICS_RULE)
    assert (result.status, result.title) == (sc.INFO, "no-limit test metrics")
    assert result.summary == ("no-limit test L-T1: 13 signals (9 AI-approved), 6 sent, 5 filled, 5 trades, win rate"
                              " 60.00%, mean R 0.44, P&L 202.50; positions at once max 3; last flat 15:45:03 ET")
    details = text_of(result)
    assert ("total: signals 13; AI 9 approved, 4 rejected (top: \"Gap faded below VWAP.\" x2, \"Extended above"
            " VWAP.\" x1, \"budget_exhausted\" x1), cost $0.12") in details
    assert "positions at once p50 1 p95 3 max 3" in details
    assert ("trade VWAP_PB_V2 CCC (orders 115-117) sent 11:30:00 ET: 750/750 sh @ 10.0000, stop 9.90, out 10.0200"
            " (flatten) at 15:45:03 ET: P&L 15.00 = 0.20R; 0.25% of the day's volume") in details
    assert "peak buying power used 11.25% of available (min AvailableFunds 88000.00, min Cushion 0.88; 3 readings)" \
        in details
    assert "Sentinel would-fence: ORB_V2 x1" in details and "market: SPY 1.00% (range 1.40%); QQQ n/a" in details
    # Rule 7 counted the entry sent over v7c's limits; rule 9 found nothing to abort for.
    assert ("6 accepted entries (6 bot, 0 manual), each measured by its own process: 5 within v7c's limits, 1 sent"
            " over them (measured, no-limit test L-T1: v7c_day_risk_budget x1)") in rule(report, 7).summary
    assert rule(report, 9).summary == (
        "none tripped: 1 stop repair(s), longest 2s (limit 60s), 0 entries outside 09:30-16:00 ET, last position flat"
        " 15:45:03 ET (limit 15:50 ET), 0 duplicates, guards tripped: t1_guard_duplicate x1")
    assert report.exit_code == sc.EXIT_PASSED


async def test_the_json_carries_every_field_and_the_owner_text_one_verdict_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = check(await build(tmp_path, monkeypatch, RECORDED_DAY))
    document = json.loads(report.render_json())

    assert set(document) == {"schema", "schema_version", "session_date", "mode", "verdict", "exit_code", "rules",
                             "metrics", "owner_text", "inputs", "missing"}
    assert (document["schema"], document["schema_version"], document["session_date"], document["mode"],
            document["verdict"], document["exit_code"]) == (
        "autotrader-v1/session-check", 1, "2026-11-02", "no_limit_test", "PASS", 0)
    assert [item["number"] for item in document["rules"]] == list(range(1, 10))
    assert all(set(item) == {"number", "title", "status", "summary", "details"} for item in document["rules"])
    assert set(document["metrics"]) == {"mode", "strategies", "total", "account", "definitions"}
    assert document["metrics"]["definitions"] == dict(sc.METRIC_DEFINITIONS)
    assert set(sc.METRIC_DEFINITIONS) <= {*EXPECTED_TOTAL, "quantiles", "r", "pct_day_volume"}
    assert document["metrics"]["total"] == EXPECTED_TOTAL  # the replay engine reads exactly the hand count
    assert document["metrics"]["account"] == EXPECTED_ACCOUNT
    assert set(document["metrics"]["strategies"]) == {"GAP_GO_V2", "MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2"}
    for name, expected in EXPECTED.items():
        assert document["metrics"]["strategies"][name] == expected
    owner = report.owner_text().splitlines()
    assert owner[0] == "L-T1 2026-11-02: clean, no abort rule tripped (session check PASS, 1 warning)"
    assert owner[1] == ("total: 13 signals, 6 sent, 5 filled, 5 trades, win 60.00%, mean R 0.44, P&L 202.50;"
                        " positions at once max 3; last flat 15:45:03")
    assert owner[2:] == ["GAP_GO_V2: 0 sent, 0 trades, P&L 0, mean R n/a",
                         "MOMO_HOD_V2: 3 sent, 2 trades, P&L 150.00, mean R 0.50",
                         "ORB_V2: 2 sent, 2 trades, P&L 37.50, mean R 0.50",
                         "VWAP_PB_V2: 1 sent, 1 trades, P&L 15.00, mean R 0.20"]
    assert document["owner_text"] == "\n".join(owner)


async def test_the_cli_prints_the_json_and_the_owner_text_with_the_same_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.cli import main

    paths = await build(tmp_path, monkeypatch, replace(RECORDED_DAY, extra=(
        *RECORDED_DAY.extra, ("15:46:00", "CRITICAL", f"CCC: still open after flatten (1 sh) — act in TWS"
                                                     f" {FAKE_ACCOUNT}"))))
    base = ["session", "check", "--date", str(MONDAY), "--logs", str(paths.logs), "--data", str(paths.data)]

    assert main([*base, "--json"]) == sc.EXIT_FAILED  # rule 2: not flat after the flatten
    printed = capsys.readouterr().out
    document = json.loads(printed)
    assert (document["verdict"], document["exit_code"], document["metrics"]["total"]["orders"]["sent"]) == ("FAIL", 1, 6)
    assert FAKE_ACCOUNT not in printed and "DU1234567" in printed  # masked like the text report
    assert main([*base, "--owner"]) == sc.EXIT_FAILED
    owner = capsys.readouterr().out
    assert owner.startswith("L-T1 2026-11-02: session check FAIL: rule 2 attribution and close-out: 1 problem(s)")
    assert FAKE_ACCOUNT not in owner
    with pytest.raises(SystemExit):
        main([*base, "--json", "--owner"])  # one output at a time
    capsys.readouterr()
    missing = ["session", "check", "--date", str(MONDAY), "--logs", str(tmp_path / "none"), "--data",
               str(paths.data), "--json"]
    assert main(missing) == sc.EXIT_INPUTS_MISSING
    document = json.loads(capsys.readouterr().out)
    assert (document["verdict"], document["rules"], document["metrics"]) == ("INPUTS MISSING", [], None)


# --- rule 3: the test's rows and the deadlines; the counts are measured --------------------------------


async def test_rule_3_reports_the_counts_and_fails_only_a_missed_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Three ORB entries and two MOMO brackets at once: v7c's caps would refuse both, the test measures them.
    many = (*CLEAN, orb("GGG", "10:00:00", "10:40:00", "51.00"), orb("HHH", "11:00:00", "11:30:00", "51.00"),
            momo("III", "10:00:00", "10:30:00", "20.40"))
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=many)))
    result = rule(report, 3)

    assert statuses(report) == ALL_CLEAN, report.render()
    assert result.summary.startswith("no-limit test L-T1: the test's rows, every entry before its last-entry time;")
    assert "ORB_V2: open at most 2, entries 3 (no cap: measured), last entry 11:00:00 ET (cutoff 15:30 ET)" in \
        result.summary
    assert "MOMO_HOD_V2: open at most 2, entries 2 (no cap: measured)" in result.summary
    assert "all identities: open at most 4 (no account-wide cap)" in result.details

    late = (*CLEAN, orb("LAT", "15:30:00", "15:45:03", "50.10", "flatten"))
    report = check(await build(tmp_path / "late", monkeypatch, T1Day(signals=late)))
    assert rule(report, 3).status == sc.FAIL and report.exit_code == sc.EXIT_FAILED
    assert ("ORB_V2 LAT (orders 109-111): sent 15:30:00 ET, at or after its last entry 15:30 ET"
            in rule(report, 3).summary)
    momo_late = (*CLEAN, momo("MLT", "15:00:00", "15:45:03", "20.10", "flatten"))
    report = check(await build(tmp_path / "momo", monkeypatch, T1Day(signals=momo_late)))
    assert "MOMO_HOD_V2 MLT (orders 109-111): sent 15:00:00 ET, at or after its last entry 15:00 ET" in \
        rule(report, 3).summary


async def test_rule_3_fails_a_capped_row_a_mode_line_without_the_test_rows_and_mixed_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capped = tuple(line.replace("caps ORB_V2 positions=none entries/day=none", "caps ORB_V2 positions=1 entries/day=1")
                   for line in T1_CAPS_LINES)
    report = check(await build(tmp_path / "capped", monkeypatch, T1Day(signals=CLEAN, caps_lines=capped)))
    assert rule(report, 3).status == sc.FAIL
    assert ("the bridge logged caps ORB_V2 positions=1 entries/day=1 last_entry=close-30m, the approved no-limit test"
            " L-T1 row is positions=none entries/day=none last_entry=close-30m") in rule(report, 3).summary

    # Uncapped rows without the mode line: a v7c process holds them to v7c's rows.
    report = check(await build(tmp_path / "no-mode", monkeypatch, T1Day(signals=CLEAN, mode_line=False)))
    assert rule(report, 3).status == sc.FAIL and report.mode == sc.V7C_MODE
    assert "the approved v7c row is positions=3 entries/day=none last_entry=close-60m" in text_of(rule(report, 3))

    # A second bridge process (after a restart at 12:00) that ran v7c: two modes in one session.
    report = check(await build(tmp_path / "mixed", monkeypatch, T1Day(signals=CLEAN, second_process="v7c")))
    assert "2 bridge process(es)" in report.inputs[0]
    assert rule(report, 3).status == sc.FAIL
    assert "one session ran both the v7c bridge and the no-limit test" in rule(report, 3).summary


# --- rule 4: the test sizes and the test policy ---------------------------------------------------------


def sized(identity: str, policy_quantity: int) -> int:
    from trading.risk.engine import scaled_quantity

    return scaled_quantity(policy_quantity, sc.IDENTITY_SIZE_MULTIPLIER[identity])


@pytest.mark.parametrize("policy", [pytest.param("test", marks=needs_a), "default"])
async def test_rule_4_sizes_exactly_under_an_approved_policy_and_the_shipped_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    # One entry per identity, at the shipped table's size (the test's x1 rows once T1-1 is merged).
    signals = (replace(momo("AAA", "09:45:00", "10:10:00", "20.40"), qty=sized("MOMO_HOD_V2", 750)),
               replace(orb("BBB", "09:50:00", "10:20:00", "49.50", "stop"), qty=sized("ORB_V2", 300)),
               replace(vwap("CCC", "11:30:00", "15:45:03", "10.02", "flatten"), qty=sized("VWAP_PB_V2", 1500)))
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=signals, policy=policy, recorded_inputs=True)))

    assert rule(report, 4).status == sc.PASS, report.render()
    assert rule(report, 4).summary == "3 SENT = floor(policy quantity x identity row) from recorded inputs"
    if policy == "test":
        assert NO_LIMIT_TEST_POLICY.fingerprint() in sc.APPROVED_POLICIES
        assert sc.APPROVED_POLICIES[NO_LIMIT_TEST_POLICY.fingerprint()] is NO_LIMIT_TEST_POLICY


async def test_rule_4_never_verifies_a_size_under_an_unapproved_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=CLEAN, policy="other", recorded_inputs=True)))

    assert rule(report, 4).status == sc.WARN, report.render()
    assert "its policy is not an approved policy" in text_of(rule(report, 4))
    assert "identity-row bound unchecked (policy not approved)" in text_of(rule(report, 4))


@needs_a
def test_the_approved_policies_are_the_default_and_the_test_policy() -> None:
    from trading.contracts import RiskPolicy

    assert set(sc.APPROVED_POLICIES) == {RiskPolicy().fingerprint(), NO_LIMIT_TEST_POLICY.fingerprint()}


# --- rule 5: measured, never blocking ----------------------------------------------------------------------


async def test_rule_5_is_info_with_the_entries_after_minus_2pct_and_the_loss_step_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Net liquidation 9000: the -2% line is 180; ORB (-37.50) and MOMO (-150.00) cross it at 10:50.
    losing = (orb("BBB", "09:50:00", "10:20:00", "49.50", "stop"), momo("AAA", "10:05:00", "10:50:00", "19.80", "stop"),
              vwap("CCC", "11:00:00", "15:45:03", "10.02", "flatten", measured="over:daily_loss_limit"))
    page = ("10:50:00", "CRITICAL", "t1_realized_loss_step 2%: the account's realized loss today is 187.50 (2.08% of"
            " net liquidation 9000.00) — bot entries continue (no-limit test L-T1); owner buys are refused past -2%;"
            " nothing is flattened")
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=losing, net_liq="9000.00", extra=(page,))))
    result = rule(report, 5)

    assert result.status == sc.INFO, report.render()  # never a failure (rule 4 fails 25% notional at 9000)
    assert result.summary.startswith("no-limit test L-T1, measured, never blocking: realized P&L -172.50")
    assert ("1 bot entry sent after the bot's realized loss reached 2%, 1 measured past the account's -2%"
            " (t1_over_v7c_limits daily_loss_limit); loss-step pages: 2%") in result.summary
    assert "VWAP_PB_V2 CCC (orders 106-108)" in text_of(result)
    assert rule(report, 8).status == sc.INFO and report.metrics is not None
    assert report.metrics.account.loss_step_pages == [2]


# --- rule 7: every SENT measured by its own process ----------------------------------------------------------


async def test_rule_7_fails_a_planted_unmeasured_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unmeasured = (CLEAN[0], replace(CLEAN[1], measured="none"), CLEAN[2])
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=unmeasured)))
    result = rule(report, 7)

    assert result.status == sc.FAIL and report.exit_code == sc.EXIT_FAILED, report.render()
    assert result.summary.startswith(
        "1 problem(s); first: ORB_V2 BBB (orders 103-105) sent without its measurement line (v7c_budget_ok or"
        " t1_over_v7c_limits), by a process that measures the day risk budget")


async def test_rule_7_counts_entries_over_3pct_and_still_pairs_each_with_its_own_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    over = (CLEAN[0], replace(CLEAN[1], measured="over:v7c_day_risk_budget", open_risk="2990.00"), CLEAN[2])
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=over)))
    result = rule(report, 7)

    assert result.status == sc.PASS, report.render()
    assert ("3 accepted entries (3 bot, 0 manual), each measured by its own process: 2 within v7c's limits, 1 sent"
            " over them (measured, no-limit test L-T1: v7c_day_risk_budget x1), highest 3.03% (ORB_V2 BBB (orders"
            " 103-105))") in result.summary
    assert ("ORB_V2 BBB (orders 103-105): 3.03% of net liquidation 100000.00, over v7c's limits (v7c_day_risk_budget)"
            in text_of(result))


@pytest.mark.parametrize("case", ["v7c-process", "owner-buy", "unknown-token", "inconsistent"])
async def test_rule_7_refuses_a_measured_only_line_where_v7c_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    over = replace(CLEAN[1], measured="over:v7c_day_risk_budget", open_risk="2990.00")
    spec = T1Day(signals=(CLEAN[0], over, CLEAN[2]), mode_line=case != "v7c-process",
                 caps_lines=V7C_CAPS_LINES if case == "v7c-process" else T1_CAPS_LINES)
    paths = await build(tmp_path, monkeypatch, spec)
    log = paths.logs / "bridge.log"
    text = log.read_text(encoding="utf-8")
    if case == "owner-buy":
        text = text.replace("t1_over_v7c_limits BBB (ORB_V2)", "t1_over_v7c_limits AAPL (manual #abc123)").replace(
            "v7c_day_risk_budget (source=bot)", "v7c_day_risk_budget (source=manual)")
    elif case == "unknown-token":
        text = text.replace("would refuse: v7c_day_risk_budget", "would refuse: max_positions")
    elif case == "inconsistent":
        text = text.replace("would refuse: v7c_day_risk_budget", "would refuse: v7c_unprotected_position")
    log.write_text(text, encoding="utf-8")
    result = rule(check(paths), 7)

    if case == "v7c-process":
        assert result.status == sc.FAIL
        assert "t1_over_v7c_limits from a process that did not run the no-limit test (no t1_mode line)" in \
            result.summary
    elif case == "owner-buy":
        assert result.status == sc.FAIL and "an owner buy is never measured-only, decision D8" in result.summary
    elif case == "unknown-token":
        assert result.status == sc.FAIL and "would refuse: 'max_positions'" in result.summary
    else:
        assert result.status == sc.WARN
        assert "is above 3% but the line does not name v7c_day_risk_budget" in result.summary


async def test_rule_7_fails_an_owner_buy_accepted_past_the_minus_2pct_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = budget_ok_line("AAPL", "manual #abc123", realized=Decimal("2000.01"), open_risk=Decimal("0"),
                            new=Decimal("50.10"), net_liq=NET_LIQ, source="manual")
    buy = ("12:10:00", "MANUAL", "SENT BUY 10 AAPL MKT #abc123 (order 55) with its stop 185 GTC (order 56)")
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=CLEAN, extra=(("12:10:00", "INFO", budget), buy))))

    assert rule(report, 7).status == sc.FAIL, report.render()
    assert ("accepted with the account's realized loss 2000.01 = 2.00% of net liquidation 100000.00, at or past the"
            " -2% stop") in rule(report, 7).summary
    at_line = budget_ok_line("AAPL", "manual #abc123", realized=Decimal("2000.00"), open_risk=Decimal("0"),
                             new=Decimal("50.10"), net_liq=NET_LIQ, source="manual")
    check_ = sc.parse_budget_ok(sc.LogLine("bridge.log", 1, 0, time(12, 10), "INFO", at_line))
    assert isinstance(check_, sc.BudgetCheck) and check_.problems == ()  # within the bridge's rounding


# --- rule 9: the abort rules ---------------------------------------------------------------------------------


def repair_lines(start: str, *, ended: str, outcome: str, symbol: str = "BBB", label: str = "ORB_V2",
                 reported: int | None = None) -> tuple[tuple[str, str, str], ...]:
    deadline = hms(MONDAY, start, 60)
    resubmit = (start, "CRITICAL", f"t1_stop_repair {symbol} ({label}): stop order 105 is not live (Cancelled) — 75 sh"
                f" unprotected; stop resubmitted once as order 200 at 49.50 in the target OCA group; closed at market"
                f" if it is not working by {deadline} ET; entries are refused meanwhile")
    if outcome == "repaired":
        seconds = reported if reported is not None else int((ny(MONDAY, ended) - ny(MONDAY, start)).total_seconds())
        return resubmit, (ended, "INFO", f"t1_stop_repair {symbol} ({label}): repaired — stop order 200 works at 49.50"
                                          f" {seconds}s after detection")
    return resubmit, (deadline, "CRITICAL", f"t1_stop_repair {symbol} ({label}): still unprotected 60s after detection"
                                            " (resubmitted stop 200: no status) — closing at market: target repriced to"
                                            " a marketable limit; entries stay refused until it is flat")


@pytest.mark.parametrize(("ended", "outcome", "aborts"), [
    ("10:01:00", "repaired", False),  # 60 s: the limit
    ("10:01:01", "repaired", True),  # 61 s
    ("10:00:02", "repaired", False),
    ("10:01:01", "closed", True),  # the deadline close: flat 61 s after detection
    ("10:00:30", "closed", False),  # a rejected resubmit closed at once: flat 30 s after detection
])
async def test_rule_9_fails_a_robot_position_without_a_working_stop_for_more_than_60s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ended: str, outcome: str, aborts: bool
) -> None:
    held = orb("BBB", "09:50:00", ended if outcome == "closed" else "10:20:00", "49.10",
               "stop_repair" if outcome == "closed" else "stop")
    lines = repair_lines("10:00:00", ended=ended, outcome=outcome)
    if outcome == "closed" and not aborts:
        lines = (lines[0], ("10:00:28", "CRITICAL", "t1_stop_repair BBB (ORB_V2): the resubmitted stop 200 was rejected"
                            " too (Cancelled, TWS 201 Order rejected - reason: stop price above the market) — closing"
                            " at market: target repriced to a marketable limit; entries stay refused until it is flat"))
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=(CLEAN[0], held, CLEAN[2]), extra=lines)))
    result = rule(report, 9)

    seconds = int((ny(MONDAY, ended) - ny(MONDAY, "10:00:00")).total_seconds())
    assert (result.status == sc.FAIL) is aborts, report.render()
    assert (report.exit_code == sc.EXIT_FAILED) is aborts
    phrase = f"robot position BBB (ORB_V2) without a working stop for {seconds}s (10:00:00-{ended} ET"
    assert phrase in text_of(result)
    if aborts:
        assert result.summary.startswith(f"1 problem(s); first: {phrase}")
        assert f"more than 60s" in result.summary
        assert report.owner_text().startswith(f"L-T1 2026-11-02: ABORT RULE TRIPPED: 1 problem(s); first: {phrase}")
    assert report.metrics is not None and report.metrics.account.stop_repairs["longest_s"] == seconds


async def test_rule_9_trusts_the_bridges_own_count_and_fails_a_repair_never_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = repair_lines("10:00:00", ended="10:01:00", outcome="repaired", reported=61)
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=CLEAN, extra=lines)))
    assert rule(report, 9).status == sc.FAIL and "without a working stop for 61s" in rule(report, 9).summary

    # An episode the log never resolves (no repaired line, the position never flat): FAIL.
    resubmit = repair_lines("10:00:00", ended="10:01:00", outcome="repaired")[0]
    lines = (resubmit[0], resubmit[1], resubmit[2].replace("BBB (ORB_V2)", "ZZZ (MOMO_HOD_V2)"))
    report = check(await build(tmp_path / "open", monkeypatch, T1Day(signals=CLEAN, extra=(lines,))))
    assert rule(report, 9).status == sc.FAIL
    assert ("robot position ZZZ (MOMO_HOD_V2) without a working stop since 10:00:00 ET, never repaired or flat in the"
            " log") in rule(report, 9).summary
    assert report.metrics is not None and report.metrics.account.stop_repairs["unresolved"] == 1
    # The CLOSED line of the position ends an episode: flat is protected enough.
    lines = (resubmit[0], resubmit[1], resubmit[2].replace("BBB (ORB_V2)", "CCC (VWAP_PB_V2)"))
    report = check(await build(tmp_path / "flat", monkeypatch, T1Day(signals=CLEAN, extra=(lines,))))
    assert "robot position CCC (VWAP_PB_V2) without a working stop for 20703s (10:00:00-15:45:03 ET, closed)" in \
        rule(report, 9).summary


@pytest.mark.parametrize(("sent", "aborts"), [("09:29:00", True), ("09:30:00", False)])
async def test_rule_9_fails_an_entry_outside_market_hours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sent: str, aborts: bool
) -> None:
    early = Signal("GAP_GO_V2", "DDD", 300, "25.00", "24.50", "26.00", sent, "10:30:00", "26.00")
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=(early, *CLEAN))))
    result = rule(report, 9)

    assert (result.status == sc.FAIL) is aborts, report.render()
    if aborts:
        assert result.summary.startswith("1 problem(s); first: GAP_GO_V2 DDD (orders 100-102) sent 09:29:00 ET,"
                                         " outside the XNYS session 09:30-16:00 ET: bridge.log:")
        assert result.summary.endswith(" 09:29:00 ET SENT DDD (GAP_GO_V2): BUY 300 @ 25.00 | stop 24.50 | target"
                                       " 26.00 (orders 100-102)")
    else:
        assert "0 entries outside 09:30-16:00 ET" in result.summary


@pytest.mark.parametrize("zone", ["Asia/Jerusalem", "Etc/GMT-2", "Etc/GMT-3", "America/New_York"])
@pytest.mark.parametrize(("day", "closed", "aborts"), [
    (MONDAY, "15:51:00", True), (MONDAY, "15:50:00", False), (MONDAY, "15:49:00", False),
    (HALF_DAY, "12:51:00", True), (HALF_DAY, "12:49:00", False),
])
async def test_rule_9_fails_a_position_open_past_the_close_less_10_minutes_on_any_local_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zone: str, day: date, closed: str, aborts: bool
) -> None:
    late = vwap("CCC", "11:30:00", closed, "10.02", "flatten")
    spec = T1Day(day=day, signals=(momo("AAA", "09:45:00", "10:10:00", "20.40"), late))
    with local_clock(zone):
        paths = await build(tmp_path, monkeypatch, spec)
        report = check(paths, day)
    result = rule(report, 9)

    limit = "15:50" if day == MONDAY else "12:50"
    assert (result.status == sc.FAIL) is aborts, report.render()
    if aborts:
        assert (f"VWAP_PB_V2 CCC (orders 103-105) open until {closed} ET, past {limit} ET (the close less 10 min)"
                in result.summary)
    else:
        assert f"last position flat {closed} ET (limit {limit} ET)" in result.summary


async def test_rule_9_fails_a_position_still_held_at_the_end_of_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = vwap("CCC", "11:30:00", None, None)
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=(CLEAN[0], held))))

    assert rule(report, 9).status == sc.FAIL
    assert "VWAP_PB_V2 CCC (orders 103-105) still holds shares at the end of the log (no CLOSED line), past 15:50 ET" \
        in rule(report, 9).summary
    assert report.metrics is not None
    assert report.metrics.account.close_out["open_at_end"] == ["VWAP_PB_V2 CCC (orders 103-105)"]


@pytest.mark.parametrize(("second", "aborts"), [("10:00:04", True), ("10:00:05", False)])
async def test_rule_9_fails_a_duplicate_entry_within_5s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, second: str, aborts: bool
) -> None:
    # IBKR rejected the first (a 201): a second send of the same symbol and size within 5 s is the double
    # send the duplicate guard exists for (one bracket per symbol never lets both hold shares).
    first = momo("DUP", "10:00:00", None, None, rejected=BUYING_POWER_REJECTION)
    again = momo("DUP", second, "10:10:00", "20.40")
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=(first, again))))

    assert (rule(report, 9).status == sc.FAIL) is aborts, report.render()
    if aborts:
        assert ("duplicate entry: MOMO_HOD_V2 DUP (orders 103-105) repeats MOMO_HOD_V2 DUP (orders 100-102) 4.0s later"
                " (BUY 750 DUP, window 5s)") in rule(report, 9).summary


ORDER_RATE = "t1_guard_order_rate: MOMO_HOD_V2 sent 20 entries in the last 60s (limit 20) — refused"


@pytest.mark.parametrize(("trips", "aborts"), [
    ((("10:00:00", "M21", "MOMO_HOD_V2", ORDER_RATE),), False),
    ((("10:00:00", "M21", "MOMO_HOD_V2", ORDER_RATE), ("10:00:01", "M22", "MOMO_HOD_V2", ORDER_RATE)), True),
    # The same guard on two identities: still the same guard twice in the day (plan v2's words).
    ((("10:00:00", "S1", "ORB_V2", "t1_guard_symbol_entries: S1 was entered 3 times today (limit 3) — refused"),
      ("11:00:00", "S2", "VWAP_PB_V2", "t1_guard_symbol_entries: S2 was entered 3 times today (limit 3) — refused")),
     True),
    # Two different guards once each: no abort.
    ((("10:00:00", "M21", "MOMO_HOD_V2", ORDER_RATE),
      ("11:00:00", "S2", "VWAP_PB_V2", "t1_guard_symbol_entries: S2 was entered 3 times today (limit 3) — refused")),
     False),
])
async def test_rule_9_fails_the_same_guard_tripping_twice_in_one_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trips: tuple[tuple[str, str, str, str], ...], aborts: bool
) -> None:
    skipped = tuple(Skipped(identity, symbol, clock, reason) for clock, symbol, identity, reason in trips)
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=CLEAN, skipped=skipped)))
    result = rule(report, 9)

    assert (result.status == sc.FAIL) is aborts, report.render()
    token = trips[0][3].split(":", 1)[0] if aborts else None
    if aborts:
        assert f"guard {token} tripped 2 times today" in result.summary
        assert "the same guard twice in one day" in result.summary


async def test_rule_9_counts_a_broker_rejection_halt_once_and_two_halts_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def halt(clock: str, identity: str, kind: str = "CRITICAL", restart: bool = False) -> tuple[str, str, str]:
        why = ("3 broker rejections in a row before this restart" if restart else
               f"3 broker rejections in a row, the last TWS {BUYING_POWER_REJECTION}")
        return (clock, kind, f"t1_guard_rejections {identity}: {why} — no new {identity} entries for the rest of"
                             f" {MONDAY}; other identities keep trading; its open brackets are still managed")

    refused = ("11:00:00", "SKIP", f"R3 (MOMO_HOD_V2): t1_guard_rejections: MOMO_HOD_V2 is halted for the day (3"
                                   f" broker rejections in a row, the last TWS {BUYING_POWER_REJECTION})")
    once = (halt("10:30:00", "MOMO_HOD_V2"), refused, halt("12:00:00", "MOMO_HOD_V2", "INFO", restart=True))
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=CLEAN, extra=once)))
    assert rule(report, 9).status == sc.PASS, report.render()
    assert "guards tripped: t1_guard_rejections x1" in rule(report, 9).summary

    twice = (*once, halt("13:00:00", "ORB_V2"))
    report = check(await build(tmp_path / "twice", monkeypatch, T1Day(signals=CLEAN, extra=twice)))
    assert rule(report, 9).status == sc.FAIL
    assert "guard t1_guard_rejections tripped 2 times today (MOMO_HOD_V2, ORB_V2)" in rule(report, 9).summary


async def test_a_v7c_day_keeps_v7c_semantics_and_still_checks_the_abort_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """deploy undo: a process without the mode line is v7c; rules 3 and 5 enforce, rule 8 is the baseline."""
    spec = T1Day(signals=(*CLEAN, orb("GGG", "13:00:00", "13:30:00", "51.00")), caps_lines=V7C_CAPS_LINES,
                 budget_line=V7C_BUDGET_LINE, mode_line=False, policy="default")
    report = check(await build(tmp_path, monkeypatch, spec))

    assert report.mode == sc.V7C_MODE
    assert rule(report, 3).status == sc.FAIL and "ORB_V2: 2 entries sent, cap 1/day" in rule(report, 3).summary
    assert rule(report, 5).status == sc.PASS
    assert rule(report, 8).summary.startswith("v7c (baseline): ")
    assert rule(report, 9).status == sc.PASS
    assert report.owner_text().startswith("v7c 2026-11-02: session check FAIL: rule 3 caps")


# --- inputs of rule 8 that may be absent ---------------------------------------------------------------------


async def test_rule_8_says_why_an_input_is_absent_and_never_fails_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=CLEAN)))
    assert report.metrics is not None
    account = report.metrics.account.as_json()

    assert statuses(report) == ALL_CLEAN, report.render()
    assert account["sentinel_would_fence"] == {"status": "n/a", "reason": "no notifier.log"}
    assert account["buying_power"]["samples"] == 0 and account["buying_power"]["peak_used_pct"] is None
    assert account["market"]["SPY"]["status"] == "n/a"
    assert all(item["pct_day_volume"] is None for item in report.metrics.total.as_json()["trades"])


# --- pins: the checker's mirrors of the bridge, the sentinel, the shadow service ------------------------------


def test_the_v7c_fixture_lines_are_the_v7c_bridges_own(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = {name: pb.Caps(caps.max_positions, caps.max_entries_per_day, caps.last_entry_before_close)
            for name, caps in sc.BRIDGE_IDENTITY_CAPS.items()}
    monkeypatch.setattr(pb, "IDENTITY_CAPS", MappingProxyType(rows))
    if B_MERGED:
        monkeypatch.setattr(pb, "NO_LIMIT_TEST", False)
    assert tuple(pb.caps_lines()) == V7C_CAPS_LINES
    assert pb.budget_line() == V7C_BUDGET_LINE
    for line in V7C_CAPS_LINES[1:]:
        assert sc._CAPS.match(line) is not None


def test_the_checker_reads_every_bridge_caps_line_shape() -> None:
    for line in T1_CAPS_LINES[1:]:
        found = sc._CAPS.match(line)
        assert found is not None and (found.group("positions"), found.group("entries")) == ("none", "none")
    assert sc.NO_LIMIT_TEST_IDENTITY_CAPS == {
        name: sc.IdentityCaps(None, None, caps.last_entry_before_close) for name, caps in sc.BRIDGE_IDENTITY_CAPS.items()
    }
    assert sc._BUDGET_RULE.match(T1_BUDGET_LINE) is not None


@needs_b
def test_the_checker_mirrors_the_no_limit_bridge() -> None:
    assert pb.NO_LIMIT_TEST is True and pb.NO_LIMIT_TEST_LABEL == sc.NO_LIMIT_TEST_LABEL
    assert {name: (caps.max_positions, caps.max_entries_per_day, caps.last_entry_before_close)
            for name, caps in pb.IDENTITY_CAPS.items()} == {
        name: (caps.max_positions, caps.max_entries_per_day, caps.last_entry_before_close)
        for name, caps in sc.NO_LIMIT_TEST_IDENTITY_CAPS.items()}
    assert tuple(pb.caps_lines()) == T1_CAPS_LINES
    assert (pb.budget_line(), pb.mode_line()) == (T1_BUDGET_LINE, T1_MODE_LINE)
    tokens = ("T1_MODE", "T1_OVER_V7C_LIMITS", "T1_ACCOUNT_VALUES", "T1_LOW_CUSHION", "T1_REALIZED_LOSS_STEP",
              "T1_BROKER_REJECTED", "T1_STOP_REPAIR", "T1_UNPROTECTED_MANUAL", "T1_GUARD_ORDER_RATE",
              "T1_GUARD_REJECTIONS", "T1_GUARD_SYMBOL_ENTRIES", "T1_GUARD_DUPLICATE", "T1_GUARD_OUTSIDE_HOURS",
              "T1_GUARD_TOKENS")
    assert {name: getattr(sc, name) for name in tokens} == {name: getattr(pb, name) for name in tokens}
    assert (sc.STOP_REPAIR_DEADLINE, sc.GUARD_DUPLICATE_WINDOW) == (pb.STOP_REPAIR_DEADLINE, pb.GUARD_DUPLICATE_WINDOW)
    assert sc.ACKNOWLEDGED_STATUSES <= pb.LIVE_ORDER_STATUSES | {"Filled"}


def test_the_checkers_other_mirrors() -> None:
    from trading.operations.shadow_service import IBKR_API_VOLUME_MULTIPLIER
    from trading.research import bars_store

    assert sc.FLAT_BY_BEFORE_CLOSE == pb.VERIFY_BEFORE_CLOSE  # 15:50 ET: IBKR's Reg T end-of-day check
    assert sc.BAR_VOLUME_MULTIPLIER == IBKR_API_VOLUME_MULTIPLIER
    assert sc.MAX_BARS_FILE_BYTES == bars_store._MAX_FILE_BYTES
    assert 'f"decision expired (' in inspect.getsource(pb.Bridge._expiry_problem)  # the expired-unsent SKIP reason


async def test_the_sentinel_log_only_line_is_what_rule_8_counts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from trading.operations import sentinel

    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", False)
    with caplog.at_level(logging.WARNING, logger="autotrader.shadow.sentinel"):
        outcome = await sentinel.fence_identity(None, identity="ORB_V2", reason="sentinel: daily loss 1.30 R")
    assert outcome == sentinel.WOULD_FENCE
    [message] = [record.getMessage() for record in caplog.records if record.name == "autotrader.shadow.sentinel"]
    assert message.startswith(sc.SENTINEL_WOULD_FENCE) and message == "sentinel would fence ORB_V2: daily loss 1.30 R"


@needs_b
def test_the_bridges_own_t1_lines_parse_in_the_checker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """LEARNED "Test fixtures copy the real log format": the merged bridge's measured-only line, its
    stop repair, its rejection, its account values and a guard SKIP, through the checker's parsers."""
    from tests.test_paper_bridge import DECIDED, Clock, connected_broker, decision_row, events, make_bridge, v7c_world
    from tests.test_paper_bridge_t1 import repair_world, reject_entry

    bridge, broker, _ = v7c_world(tmp_path / "over", held=9965, decisions=(decision_row("d1", "AAPL"),))
    capsys.readouterr()
    bridge.tick()
    out = events(capsys)
    [over] = [text for kind, text in out if kind == "INFO" and text.startswith(sc.T1_OVER_V7C_LIMITS)]
    parsed = sc.parse_over_limits(sc.LogLine("bridge.log", 1, 0, None, "INFO", over))
    assert isinstance(parsed, sc.OverCheck) and parsed.tokens == ("v7c_day_risk_budget",) and not parsed.problems
    assert over == over_line("AAPL", "MOMO_HOD_V2", realized=Decimal("0"), open_risk=Decimal("19930.00"),
                             new=Decimal("50.00"), net_liq="665000.00", tokens="v7c_day_risk_budget")
    [sent] = [text for kind, text in out if kind == "SENT"]
    found = sc._SENT.match(sent)
    assert found is not None and ("bot", found["symbol"], found["label"]) == parsed.key
    assert any(sc._ACCOUNT_VALUE.search(text) for kind, text in out if text.startswith(sc.T1_ACCOUNT_VALUES))

    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, item = repair_world(tmp_path / "repair", clock=clock)
    capsys.readouterr()
    bridge.tick()
    clock.advance(2)
    bridge.tick()
    repairs = [text for _, text in events(capsys) if text.startswith(sc.T1_STOP_REPAIR)]
    assert len(repairs) == 2
    first, done = (sc._REPAIR.match(text) for text in repairs)
    assert first is not None and sc._REPAIR_DEADLINE.search(first["rest"]) is not None
    assert done is not None and sc._REPAIRED.match(done["rest"])["seconds"] == "2"

    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path / "reject", broker, clock=Clock(DECIDED + timedelta(seconds=5)),
                         decisions=(decision_row("d1", "AAA", con_id=7001),))
    bridge.tick()
    capsys.readouterr()
    reject_entry(broker, 100)
    bridge.tick()
    out = events(capsys)
    assert any(sc._BROKER_REJECTED.match(text) for _, text in out)
    assert any(sc._REJECTED_ENTRY.match(text) for kind, text in out if kind == "INFO")
    assert any(sc._ORDER_STATUS.match(text) for kind, text in out if kind == "INFO")


# --- the replay keeps the bridge's own view of a rejected entry --------------------------------------------


async def test_a_rejected_entry_holds_no_slot_after_its_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    signals = (Signal("MOMO_HOD_V2", "EEE", 500, "30.00", "29.70", "30.60", "10:00:00", None, None,
                      rejected=BUYING_POWER_REJECTION), momo("AAA", "10:05:00", "10:30:00", "20.40"))
    report = check(await build(tmp_path, monkeypatch, T1Day(signals=signals)))

    assert statuses(report) == {**ALL_CLEAN, 4: sc.WARN}, report.render()
    assert report.metrics is not None
    assert report.metrics.strategies["MOMO_HOD_V2"].open_samples == [1, 1]  # EEE was gone when AAA was sent
    assert report.metrics.total.as_json()["orders"]["rejected"] == {"201": 1}


def test_the_fixture_signals_are_sized_inside_either_identity_table() -> None:
    """Rule 4's bound holds for the fixtures under v7c's rows and the test's x1 rows alike."""
    from trading.contracts import PortfolioSnapshot, RiskPolicy

    v7c = {"MOMO_HOD_V2": Decimal("1"), "ORB_V2": Decimal("0.25"), "VWAP_PB_V2": Decimal("0.5"),
           "GAP_GO_V2": Decimal("0.5")}
    for signal in (*CLEAN, *DAY_SIGNALS):
        for row in (v7c[signal.identity], Decimal("1")):
            portfolio = PortfolioSnapshot(observed_at=ny(MONDAY, "10:00:00"),
                                          net_liquidation=Decimal(NET_LIQ) * Decimal("1.1"))
            bound = sc.scaled_quantity(sc._policy_quantity(RiskPolicy(), portfolio, Decimal(signal.entry),
                                                           Decimal(signal.stop)), row)
            assert signal.qty <= bound, (signal.symbol, row)


async def test_a_metrics_failure_is_a_warning_and_never_hides_the_safety_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = await build(tmp_path, monkeypatch, T1Day(signals=CLEAN))

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("planted")

    monkeypatch.setattr(sc, "day_metrics", broken)
    report = check(paths)

    assert statuses(report) == {**ALL_CLEAN, 8: sc.WARN}, report.render()
    assert rule(report, 8).summary == "the metrics could not be computed (RuntimeError: planted)"
    assert report.metrics is None and report.exit_code == sc.EXIT_PASSED
    assert json.loads(report.render_json())["metrics"] is None
    assert report.owner_text() == "L-T1 2026-11-02: clean, no abort rule tripped (session check PASS, 2 warnings)\n"
