"""W6.4: the read-only session checker (trading/operations/session_check.py).

Each fixture day is written the way production writes it: bridge.log lines in
the bridge's own format under the supervisor's banners (with an earlier day in
an archive), supervisor.log with a rotation's carried lines, the bridge state
from the bridge's own ``Bracket`` dataclass, and a real migrated ledger. Every
account id is fake (DU1234567, DU7777777).
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType

import pytest

from trading.operations import session_check as sc

BRIDGE_PATH = Path(__file__).resolve().parents[1] / "scripts_local" / "paper_bridge.py"
DAY = date(2026, 10, 19)
PREVIOUS = date(2026, 10, 16)
NET_LIQ = "100000.00"
SETUP = "a" * 64
FAKE_ACCOUNT = "DU7777777"
BRIDGE_SHA = "7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4"


def _load_bridge() -> ModuleType:
    spec = importlib.util.spec_from_file_location("paper_bridge_session_check", BRIDGE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


pb = _load_bridge()


@dataclass(frozen=True)
class Trade:
    identity: str
    symbol: str
    qty: int
    entry: str
    stop: str
    target: str
    sent: str  # HH:MM:SS ET
    closed: str  # HH:MM:SS ET
    exit_price: str
    exit_reason: str = "target"  # target | stop | flatten

    @property
    def pnl(self) -> Decimal:
        return ((Decimal(self.exit_price) - Decimal(self.entry)) * self.qty).quantize(Decimal("0.01"))

    @property
    def decision_id(self) -> str:
        return f"decision-{self.identity}-{self.symbol}"


# Sized at net liquidation 100000 with the default RiskPolicy (0.5% risk, 15% notional):
# GAP 600 x0.5, ORB 300 x0.25, MOMO 750 x1, VWAP 1500 x0.5.
GAP = Trade("GAP_GO_V2", "DDD", 300, "25.00", "24.50", "26.00", "09:40:00", "10:30:00", "26.00", "target")
ORB = Trade("ORB_V2", "BBB", 75, "50.00", "49.50", "51.00", "09:50:00", "10:20:00", "49.50", "stop")
MOMO = Trade("MOMO_HOD_V2", "AAA", 750, "20.00", "19.80", "20.40", "10:05:00", "11:00:00", "20.40", "target")
VWAP = Trade("VWAP_PB_V2", "CCC", 750, "10.00", "9.90", "10.20", "11:00:00", "15:45:03", "10.02", "flatten")
CLEAN = (GAP, ORB, MOMO, VWAP)
SHADOW_SKIPS = (("10:07:00", "EEE", "AP_MOMO_HOD_V1"), ("10:08:00", "FFF", "ORB_V1"))


@dataclass
class Day:
    logs: Path
    data: Path
    db: Path


@dataclass
class Spec:
    trades: tuple[Trade, ...] = CLEAN
    extra: tuple[tuple[str, str, str], ...] = ()
    flat: bool = True
    recorded_inputs: bool = False
    net_liq: str = NET_LIQ
    failed_first_process: bool = False
    fences: tuple[tuple[str, int, str, str], ...] = ()
    data_name: str = "data"
    skips: tuple[tuple[str, str, str], ...] = SHADOW_SKIPS
    # Bridge v7c logs its day risk budget at connect and a v7c_budget_ok line right before each SENT;
    # an older bridge (v7b) logs neither. budget_lines=False drops the v7c_budget_ok lines alone.
    version: str = "v7c"
    budget_lines: bool = True
    budget_open: tuple[tuple[str, str], ...] = ()  # (symbol, open risk) logged instead of the replayed one


def _clock(text: str, seconds: int = 0) -> str:
    moment = datetime.combine(DAY, time.fromisoformat(text)) + timedelta(seconds=seconds)
    return moment.strftime("%H:%M:%S")


def _utc(text: str, seconds: int = 0) -> datetime:
    local = datetime.combine(DAY, time.fromisoformat(text), tzinfo=sc.NY) + timedelta(seconds=seconds)
    return local.astimezone(timezone.utc)


def _line(clock: str, kind: str, text: str) -> str:
    return f"{clock} ET  {kind} {text}"


def _four(value: str) -> str:
    return str(Decimal(value).quantize(Decimal("0.0001")))


def _bridge_header(day: date, clock: str, net_liq: str | None, *, connected: bool = True,
                   version: str = "v7c") -> list[str]:
    lines = [_line(clock, "INFO", f"bridge sha256={BRIDGE_SHA}")]
    if not connected:
        return lines
    lines += [
        _line(clock, "INFO", f"PAPER bridge {version} connected (all accounts paper). Managing brackets for {day}"
              " until 15:55 ET."),
        *(_line(clock, "INFO", text) for text in pb.caps_lines()),
        _line(clock, "INFO", "fenced identities = none"),
        *([_line(clock, "INFO", pb.budget_line())] if version == "v7c" else []),
        _line(clock, "INFO", f"state /home/test/.local/share/autotrader-v1/bridge/{day}.json: 0 brackets, 0 active"),
    ]
    if net_liq is not None:
        lines.append(_line(clock, "INFO", f"net liquidation {net_liq}; daily loss stop 2%"))
    return lines


def _banner(day: date, at: datetime) -> str:
    return (f"=== {at.isoformat()} supervisor starts bridge: /repo/backend/.venv/bin/python"
            f" /repo/backend/scripts_local/paper_bridge.py --date {day}")


def _risk(trade: Trade) -> Decimal:
    return trade.qty * (Decimal(trade.entry) - Decimal(trade.stop))


def budget_ok_line(symbol: str, label: str, *, realized: Decimal, open_risk: Decimal, new: Decimal, net_liq: str,
                   source: str = "bot") -> str:
    """The bridge's v7c_budget_ok text, built from its own Budget (process_decision / ManualDesk.buy;
    test_paper_bridge's test_v7c_budget_lines_parse_in_session_check feeds the real lines to the checker)."""
    budget = pb.Budget(realized_loss=realized, open_risk=open_risk, new_risk=new, net_liq=Decimal(net_liq))
    return (f"{pb.V7C_BUDGET_OK} {symbol} ({label}): {budget.text()} <= {pb.DAY_RISK_BUDGET_PCT}% of"
            f" {pb.money(budget.net_liq)} (source={source})")


def _budget_ok(spec: Spec, trade: Trade) -> str:
    """The trade's budget line at its send: the risk of the brackets still open then, and the realized loss."""
    sent = time.fromisoformat(trade.sent)
    earlier = [other for other in spec.trades if time.fromisoformat(other.sent) < sent]
    open_risk = sum((_risk(other) for other in earlier if time.fromisoformat(other.closed) > sent), Decimal("0"))
    realized = sum((other.pnl for other in earlier if time.fromisoformat(other.closed) <= sent), Decimal("0"))
    open_risk = Decimal(dict(spec.budget_open).get(trade.symbol, open_risk))
    return budget_ok_line(trade.symbol, trade.identity, realized=max(Decimal("0"), -realized), open_risk=open_risk,
                          new=_risk(trade), net_liq=spec.net_liq)


def _events(spec: Spec) -> list[tuple[str, str, str]]:
    events: list[tuple[str, str, str]] = []
    for index, trade in enumerate(spec.trades):
        parent = 10 + 3 * index
        if spec.version == "v7c" and spec.budget_lines:
            events.append((trade.sent, "INFO", _budget_ok(spec, trade)))  # stable sort: right before its SENT
        events += [
            (trade.sent, "SENT", f"{trade.symbol} ({trade.identity}): BUY {trade.qty} @ {trade.entry} | stop"
             f" {trade.stop} | target {trade.target} (orders {parent}-{parent + 2})"),
            (_clock(trade.sent, 1), "INFO", f"order {parent}: Submitted filled=0.0 remaining={trade.qty}.0 avg=0.0"),
            (_clock(trade.sent, 2), "FILL", f"{trade.symbol}: {trade.qty}/{trade.qty} sh @ {_four(trade.entry)}"
             f" ({trade.identity})"),
        ]
        if trade.exit_reason == "flatten":
            events.append(("15:45:01", "FLATTEN", f"{trade.symbol}: target repriced to {trade.exit_price} for"
                           f" {trade.qty} sh (bid {trade.exit_price})"))
        events += [
            (trade.closed, "EXIT", f"{trade.symbol}: {trade.qty} sh @ {_four(trade.exit_price)} via"
             f" {trade.exit_reason} | P&L {trade.pnl}"),
            (trade.closed, "CLOSED", f"{trade.symbol}: P&L {trade.pnl} ({trade.exit_reason})"),
        ]
    events += [(clock, "SKIP", f"{symbol} ({name}): shadow-only strategy") for clock, symbol, name in spec.skips]
    events.append(("15:45:00", "INFO", "15:45 ET: flattening all engine positions"))
    if spec.flat:
        events.append(("15:50:00", "FLAT", "no open engine positions"))
    events += list(spec.extra)
    return sorted(events, key=lambda event: event[0])


def _write_logs(logs: Path, spec: Spec) -> None:
    logs.mkdir(parents=True)
    previous = [
        _banner(PREVIOUS, datetime(2026, 10, 16, 13, 25, tzinfo=timezone.utc)),
        *_bridge_header(PREVIOUS, "09:25:00", NET_LIQ),
        _line("10:00:00", "SENT", "OLD (MOMO_HOD_V2): BUY 10 @ 10.00 | stop 9.90 | target 10.20 (orders 1-3)"),
        _line("15:55:00", "CRITICAL", "OLD: still open after flatten (10 sh) — act in TWS"),
        _line("15:55:00", "INFO", "bridge stopped (exit 0)"),
    ]
    (logs / f"bridge.log.{PREVIOUS}").write_text("\n".join(previous) + "\n", encoding="utf-8")
    today: list[str] = []
    supervisor_day: list[str] = [f"{datetime(2026, 10, 19, 11, 55, tzinfo=timezone.utc).isoformat()} SESSION_START date={DAY}"]
    if spec.failed_first_process:
        start = datetime(2026, 10, 19, 13, 25, tzinfo=timezone.utc)
        today += [
            _banner(DAY, start),
            *_bridge_header(DAY, "09:25:00", None, version=spec.version),
            _line("09:25:10", "CRITICAL", f"net liquidation unavailable for {FAKE_ACCOUNT} — new entries blocked;"
                  " open brackets are still managed"),
            _line("09:25:20", "CRITICAL", "bridge error ReconciliationError: TWS did not finish reporting open"
                  " orders, executions and positions — exit 75 for a supervised restart"),
            "Traceback (most recent call last):",
            "ReconciliationError: TWS did not finish reporting open orders, executions and positions",
            _line("09:25:20", "INFO", "bridge stopped (exit 75)"),
        ]
        supervisor_day += [f"{start.isoformat()} CHILD_START bridge pid=200",
                           f"{(start + timedelta(seconds=21)).isoformat()} CHILD_EXIT bridge code=75 restart_in=15"]
    start = datetime(2026, 10, 19, 13, 26, tzinfo=timezone.utc)
    today += [_banner(DAY, start), *_bridge_header(DAY, "09:26:00", spec.net_liq, version=spec.version)]
    today += [_line(clock, kind, text) for clock, kind, text in _events(spec)]
    realized = sum((trade.pnl for trade in spec.trades), Decimal("0"))
    today += [_line("15:55:00", "INFO", f"session end: realized P&L {realized}"),
              _line("15:55:00", "INFO", "bridge stopped (exit 0)")]
    (logs / "bridge.log").write_text("\n".join(today) + "\n", encoding="utf-8")
    final_exit = f"{datetime(2026, 10, 19, 19, 55, tzinfo=timezone.utc).isoformat()} CHILD_EXIT bridge code=0 restart_in=none"
    supervisor_day += [f"{start.isoformat()} CHILD_START bridge pid=201", final_exit]
    archive = [
        f"{datetime(2026, 10, 16, 13, 25, tzinfo=timezone.utc).isoformat()} CHILD_START bridge pid=100",
        f"{datetime(2026, 10, 16, 19, 55, tzinfo=timezone.utc).isoformat()} CHILD_EXIT bridge code=0 restart_in=none",
        *supervisor_day,
    ]
    (logs / f"supervisor.log.{DAY}").write_text("\n".join(archive) + "\n", encoding="utf-8")
    # The 18:35 ET rotation copied the newest state lines to the top of the new file.
    rotated = datetime(2026, 10, 19, 22, 35, tzinfo=timezone.utc).isoformat()
    current = [
        f"{datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc).isoformat()} SUPERVISOR_START pid=7 mode=daily",
        final_exit,
        f"{rotated} LOG_ROTATED date={DAY} renamed=3 truncated=0 pruned=0 carried=2",
    ]
    (logs / "supervisor.log").write_text("\n".join(current) + "\n", encoding="utf-8")


def _write_state(data: Path, spec: Spec) -> None:
    brackets = {}
    for index, trade in enumerate(spec.trades):
        parent = 10 + 3 * index
        bracket = pb.Bracket(
            decision_id=trade.decision_id, symbol=trade.symbol, con_id=1000 + index, primary_exchange="NASDAQ",
            strategy=pb.strategy_family(trade.identity), qty=trade.qty, entry=trade.entry, stop=trade.stop,
            target=trade.target, parent=parent, target_id=parent + 1, stop_id=parent + 2,
            sent_at=_utc(trade.sent).isoformat(), status="closed", filled=trade.qty, avg_entry=_four(trade.entry),
            exit_filled=trade.qty, avg_exit=_four(trade.exit_price), exit_reason=trade.exit_reason,
            realized_pnl=str(trade.pnl), strategy_name=trade.identity, run_id=_run_id(trade.identity),
            flatten_attempted=trade.exit_reason == "flatten", flatten_sent=trade.exit_reason == "flatten",
        )
        brackets[trade.decision_id] = pb.asdict(bracket)
    for clock, symbol, name in spec.skips:
        bracket = pb.Bracket(
            decision_id=f"decision-{name}-{symbol}", symbol=symbol, con_id=2000, primary_exchange="NYSE",
            strategy=pb.strategy_family(name), qty=100, entry="10.00", stop="9.90", target="10.20",
            skipped=True, status="skipped", skip_reason=pb.SHADOW_ONLY, strategy_name=name, run_id=_run_id(name),
        )
        brackets[bracket.decision_id] = pb.asdict(bracket)
    directory = data / "bridge"
    directory.mkdir(parents=True)
    document = {"schema": pb.STATE_SCHEMA, "session_date": DAY.isoformat(), "submitted": brackets}
    (directory / f"{DAY}.json").write_text(json.dumps(document, indent=2), encoding="utf-8")


def _run_id(identity: str) -> str:
    return f"shadow-{identity.lower()}-{DAY}"


async def _seed_ledger(db: Path, monkeypatch: pytest.MonkeyPatch, spec: Spec) -> None:
    from config import cfg
    from db.core import init_db
    from tests.fakes.legacy_strategies import insert_legacy_shadow_strategy
    from trading.contracts import (
        AccountSnapshot, AIReview, Candidate, Decision, DecisionOutcome, EngineMode, FixedUniverse,
        RiskApproval, RiskPolicy, RuleCondition, Run, ScannerRun, StrategySpec, TradeIntent, fingerprint_payload,
    )
    from trading.data import CandidateRepository, RunRepository, ScannerRunRepository
    from trading.risk.portfolio import portfolio_from_account
    from trading.storage.repository import record_decision
    from trading.storage.shadow_ledger import SQLiteShadowLedger

    monkeypatch.setattr(cfg, "DB_PATH", str(db))
    await init_db()
    decided: list[tuple[str, str, Trade | None, str, str]] = [
        (trade.identity, trade.symbol, trade, trade.sent, trade.decision_id) for trade in spec.trades
    ] + [(name, symbol, None, clock, f"decision-{name}-{symbol}") for clock, symbol, name in spec.skips]
    strategies: dict[str, str] = {}
    pending: dict[str, list[str]] = {}
    for number, (name, symbol, trade, clock, decision_id) in enumerate(decided):
        if name not in strategies:
            spec_model = StrategySpec(
                name=name, universe=FixedUniverse(symbols=("AAPL",)),
                conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
                rationale=f"{name} session-check fixture.",
            )
            strategies[name] = (await insert_legacy_shadow_strategy(spec=spec_model, owner_id="demo")).strategy_id
            run_id = _run_id(name)
            await RunRepository().save(Run(
                id=run_id, owner_id="demo", source_sha="abcdef1", strategy_id=strategies[name], strategy_version=1,
                policy_fingerprints={"risk": RiskPolicy().fingerprint()}, session_date=DAY, mode=EngineMode.SHADOW,
            ))
            parameters = {"scan_code": "HOT_BY_VOLUME", "limit": 50}
            await ScannerRunRepository().save(ScannerRun(
                id=f"{run_id}-scan", run_id=run_id, parameters=parameters,
                parameters_fingerprint=fingerprint_payload(parameters), status="completed",
                started_at=_utc("09:00:00"),
            ))
        run_id = _run_id(name)
        candidate = Candidate(id=f"{run_id}-{symbol}", scanner_run_id=f"{run_id}-scan", con_id=1000 + number,
                              symbol=symbol, primary_exchange="NASDAQ", rank=len(pending.get(run_id, [])) + 1,
                              admission_status="admitted")
        await CandidateRepository().save(candidate)
        created = _utc(clock, -2)
        portfolio_fingerprint = "e" * 64
        if spec.recorded_inputs:
            account = AccountSnapshot(
                paper_account_id="DU1234567", client_id=19, observed_at=created - timedelta(seconds=1),
                net_liquidation=Decimal(NET_LIQ), available_funds=Decimal(NET_LIQ),
                realized_day_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
            )
            await SQLiteShadowLedger().save_account_snapshot(account, run_id=run_id)
            portfolio = portfolio_from_account(account, owned_con_ids=frozenset(),
                                               pending_symbols=tuple(sorted(pending.get(run_id, []))))
            portfolio_fingerprint = portfolio.fingerprint
        pending.setdefault(run_id, []).append(symbol)
        quantity, entry, stop, target = ((trade.qty, trade.entry, trade.stop, trade.target) if trade
                                         else (100, "10.00", "9.90", "10.20"))
        review = AIReview(setup_fingerprint=SETUP, decision="PROPOSE", confidence="0.7", rationale="Fixture.",
                          model_id="fixture-model", prompt_version="fixture-prompt", response_hash="c" * 64,
                          latency_ms=5, cost_usd="0.01")
        expires = created + timedelta(seconds=15)
        intent = TradeIntent(
            execution_key=f"exec-{decision_id}", setup_fingerprint=SETUP, approval_token="t" * 32,
            expires_at=expires, strategy_id=strategies[name], strategy_version=1, symbol=symbol,
            quantity=quantity, entry_limit=entry, stop_price=stop, target_price=target,
        )
        await record_decision(decision=Decision(
            id=decision_id, run_id=run_id, candidate_id=candidate.id, strategy_id=strategies[name],
            strategy_version=1, decision_key=sha256(decision_id.encode()).hexdigest(),
            outcome=DecisionOutcome.SHADOW_ACCEPTED, setup_fingerprint=SETUP, ai_review=review,
            risk=RiskApproval(
                approved=True, intent=intent, setup_fingerprint=SETUP, ai_review_fingerprint=review.fingerprint(),
                policy_fingerprint=RiskPolicy().fingerprint(), portfolio_fingerprint=portfolio_fingerprint,
                expires_at=expires,
            ),
            created_at=created,
        ), owner_id="demo")
    if spec.fences:
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.executemany("INSERT INTO autotrader_execution_fences VALUES (?, ?, ?, ?)", spec.fences)


async def build_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: Spec | None = None) -> Day:
    spec = spec or Spec()
    day = Day(logs=tmp_path / "logs", data=tmp_path / spec.data_name, db=tmp_path / spec.data_name / "autotrader.db")
    _write_logs(day.logs, spec)
    _write_state(day.data, spec)
    await _seed_ledger(day.db, monkeypatch, spec)
    return day


def check(day: Day) -> sc.SessionReport:
    return sc.check_session(DAY, logs=day.logs, data=day.data, db=day.db)


def statuses(report: sc.SessionReport) -> dict[int, str]:
    return {rule.number: rule.status for rule in report.rules}


def rule(report: sc.SessionReport, number: int) -> sc.RuleResult:
    return next(item for item in report.rules if item.number == number)


ALL_PASS_BUT_SIZE = {1: sc.PASS, 2: sc.PASS, 3: sc.PASS, 4: sc.WARN, 5: sc.PASS, 6: sc.PASS, 7: sc.PASS}


# --- the clean day ----------------------------------------------------------------------


async def test_a_clean_four_identity_day_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = check(await build_day(tmp_path, monkeypatch))
    text = report.render()

    assert statuses(report) == ALL_PASS_BUT_SIZE, text
    assert report.exit_code == sc.EXIT_PASSED
    assert text.startswith("Session check 2026-10-19: PASS (1 warning), exit 0")
    # Without recorded inputs the size rule never passes on its own.
    assert rule(report, 4).summary.startswith("size check: inputs not recorded for 4 of 4 SENT")
    assert "4 SENT, all sendable (GAP_GO_V2 x1, MOMO_HOD_V2 x1, ORB_V2 x1, VWAP_PB_V2 x1)" in rule(report, 1).summary
    assert "AP_MOMO_HOD_V1 x1, ORB_V1 x1" in "\n".join(rule(report, 1).details)
    assert "MOMO_HOD_V2: open at most 1/3, entries 1/none" in rule(report, 3).summary
    assert "(cutoff 15:00 ET)" in rule(report, 3).summary and "(cutoff 15:30 ET)" in rule(report, 3).summary
    assert "realized P&L 577.50 = 0.58% of net liquidation 100000.00" in rule(report, 5).summary
    # Neither the archived day before (still open after flatten) nor the rotation's
    # carried supervisor lines reach this day's verdict.
    assert "OLD" not in text
    assert "supervisor: 1 bridge starts" in "\n".join(rule(report, 2).details)
    assert "ORB_V2 BBB (orders 13-15): 75 sh = SENT = intent = state" in "\n".join(rule(report, 4).details)


async def test_recorded_inputs_make_the_size_check_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = check(await build_day(tmp_path, monkeypatch, Spec(recorded_inputs=True)))

    assert set(statuses(report).values()) == {sc.PASS}, report.render()
    assert rule(report, 4).summary == "4 SENT = floor(policy quantity x identity row) from recorded inputs"
    assert ("ORB_V2 BBB (orders 13-15): 75 sh = SENT = intent = state; floor(policy 300 x x0.25) = 75 at the"
            " recorded net liquidation 100000.00") in "\n".join(rule(report, 4).details)
    assert report.render().startswith("Session check 2026-10-19: PASS, exit 0")


async def test_the_cli_runs_the_check_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    from trading.cli import main

    day = await build_day(tmp_path, monkeypatch)
    code = main(["session", "check", "--date", str(DAY), "--logs", str(day.logs), "--data", str(day.data),
                 "--db-path", str(day.db)])

    assert code == 0
    assert capsys.readouterr().out.startswith("Session check 2026-10-19: PASS (1 warning), exit 0")
    # --data alone finds the database beside the state.
    assert main(["session", "check", "--date", str(DAY), "--logs", str(day.logs), "--data", str(day.data)]) == 0


# --- rule 4: size -------------------------------------------------------------------------


@pytest.mark.parametrize("recorded", [False, True], ids=["inputs-not-recorded", "inputs-recorded"])
async def test_an_orb_sent_at_x1_size_fails_the_size_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: bool
) -> None:
    unscaled = replace(ORB, qty=300)  # the v7b.1 bug class: the x0.25 row dropped
    report = check(await build_day(tmp_path, monkeypatch, Spec(trades=(GAP, unscaled, MOMO, VWAP),
                                                               recorded_inputs=recorded)))

    assert statuses(report) == {**ALL_PASS_BUT_SIZE, 4: sc.FAIL}, report.render()
    assert report.exit_code == sc.EXIT_FAILED
    summary = rule(report, 4).summary
    if recorded:
        assert ("ORB_V2 BBB (orders 13-15): 300 sh, but floor(policy 300 x x0.25) = 75 at the recorded net"
                " liquidation 100000.00") in summary
    else:
        assert "ORB_V2 BBB (orders 13-15): 300 sh is above its x0.25 row: at most 82 sh even at net liquidation" \
               " 110000.00 (logged +10%); about x1.00 of the policy size 300 at 100000.00" in summary


async def test_a_sent_that_differs_from_its_decision_fails_the_size_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = await build_day(tmp_path, monkeypatch)
    log = day.logs / "bridge.log"
    log.write_text(log.read_text(encoding="utf-8").replace("BBB (ORB_V2): BUY 75", "BBB (ORB_V2): BUY 76"),
                   encoding="utf-8")
    report = check(day)

    assert rule(report, 4).status == sc.FAIL
    failures = "\n".join(rule(report, 4).details)
    assert "SENT 76 sh, the state 75" in failures
    assert "SENT 76 @ 50.00/49.50/51.00, its decision's intent 75 @ 50.00/49.50/51.00" in failures


# --- rule 3: caps ---------------------------------------------------------------------------


async def test_a_second_orb_entry_fails_the_caps_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    second = Trade("ORB_V2", "GGG", 75, "50.00", "49.50", "51.00", "13:00:00", "13:30:00", "51.00", "target")
    report = check(await build_day(tmp_path, monkeypatch, Spec(trades=(*CLEAN, second))))

    assert statuses(report) == {**ALL_PASS_BUT_SIZE, 3: sc.FAIL}, report.render()
    assert "ORB_V2: 2 entries sent, cap 1/day (attempts count)" in rule(report, 3).summary
    assert report.exit_code == sc.EXIT_FAILED


async def test_an_entry_after_its_cutoff_fails_the_caps_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    late = replace(MOMO, sent="15:05:00", closed="15:45:03", exit_price="20.10", exit_reason="flatten")
    report = check(await build_day(tmp_path, monkeypatch, Spec(trades=(GAP, ORB, late, VWAP))))

    assert statuses(report) == {**ALL_PASS_BUT_SIZE, 3: sc.FAIL}, report.render()
    # MOMO's own row (close - 60 min), not the session's 15:30.
    assert "MOMO_HOD_V2 AAA (orders 16-18): sent 15:05:00 ET, at or after its last entry 15:00 ET" in \
        rule(report, 3).summary


async def test_two_orb_brackets_open_at_once_fail_the_position_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlap = Trade("ORB_V2", "HHH", 75, "50.00", "49.50", "51.00", "10:00:00", "10:40:00", "51.00", "target")
    report = check(await build_day(tmp_path, monkeypatch, Spec(trades=(*CLEAN, overlap))))

    assert rule(report, 3).status == sc.FAIL
    details = "\n".join(rule(report, 3).details)
    assert "ORB_V2: 2 brackets open at once, cap 1" in details


def test_last_entry_times_follow_the_real_close_on_a_half_day() -> None:
    from trading.scheduler.sessions import market_session

    session = market_session(date(2026, 11, 27))
    assert session is not None and session.half_day
    closes_at = session.closes_at.astimezone(sc.NY)
    assert {name: f"{sc.entry_deadline(name, closes_at):%H:%M}" for name in sc.BRIDGE_IDENTITY_CAPS} == {
        "MOMO_HOD_V2": "12:00", "ORB_V2": "12:30", "VWAP_PB_V2": "12:30", "GAP_GO_V2": "12:30",
    }


# --- rule 2: attribution and close-out ------------------------------------------------------


@pytest.mark.parametrize(
    "line, reason",
    [
        (("10:15:00", "FILL", "ZZZ: 10/10 sh @ 5.0000 (MOMO_HOD_V2)"), "FILL without a SENT for its symbol"),
        (("10:15:00", "FILL", "AAA: 750/750 sh @ 20.0000 (ORB_V2)"), "FILL names 'ORB_V2', its SENT MOMO_HOD_V2"),
        (("10:15:00", "EXIT", "ZZZ: 10 sh @ 5.1000 via target | P&L 1.00"), "EXIT without a SENT for its symbol"),
    ],
    ids=["fill-without-sent", "fill-for-another-identity", "exit-without-sent"],
)
async def test_an_unattributed_fill_fails_the_attribution_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: tuple[str, str, str], reason: str
) -> None:
    report = check(await build_day(tmp_path, monkeypatch, Spec(extra=(line,))))

    assert statuses(report)[2] == sc.FAIL, report.render()
    assert f"unattributed: {reason}" in rule(report, 2).summary
    assert report.exit_code == sc.EXIT_FAILED


async def test_a_missing_flat_fails_the_attribution_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = check(await build_day(tmp_path, monkeypatch, Spec(flat=False)))

    assert statuses(report) == {**ALL_PASS_BUT_SIZE, 2: sc.FAIL}, report.render()
    assert "no FLAT line at or after the 15:45 ET flatten" in rule(report, 2).summary


async def test_still_open_after_flatten_fails_the_attribution_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alert = ("15:46:00", "CRITICAL", "CCC: still open after flatten (750 sh) — act in TWS")
    report = check(await build_day(tmp_path, monkeypatch, Spec(extra=(alert,))))

    assert rule(report, 2).status == sc.FAIL
    assert "not flat: bridge.log" in rule(report, 2).summary and "still open after flatten" in rule(report, 2).summary


# --- rule 1: identities -------------------------------------------------------------------------


async def test_a_shadow_only_sent_fails_the_identity_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shadow = Trade("AP_MOMO_HOD_V1", "EEE", 750, "20.00", "19.80", "20.40", "10:07:00", "10:50:00", "20.40", "target")
    report = check(await build_day(tmp_path, monkeypatch, Spec(trades=(*CLEAN, shadow), skips=SHADOW_SKIPS[1:])))

    assert rule(report, 1).status == sc.FAIL, report.render()
    assert "SENT for a shadow-only name 'AP_MOMO_HOD_V1'" in rule(report, 1).summary
    assert "state bracket decision-AP_ EEE was sent for the shadow-only name 'AP_MOMO_HOD_V1'" in \
        "\n".join(rule(report, 1).details)
    assert report.exit_code == sc.EXIT_FAILED


async def test_a_sendable_identity_skipped_as_shadow_only_fails_the_identity_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = check(await build_day(tmp_path, monkeypatch, Spec(
        trades=(GAP, ORB, MOMO), skips=(*SHADOW_SKIPS, ("11:00:00", "CCC", "VWAP_PB_V2")),
    )))

    assert rule(report, 1).status == sc.FAIL
    assert "sendable VWAP_PB_V2 skipped as shadow-only" in "\n".join(rule(report, 1).details)


# --- rules 5 and 6 -----------------------------------------------------------------------------------


async def test_an_entry_after_the_daily_loss_stop_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    losing = replace(MOMO, sent="10:05:00", closed="10:50:00", exit_price="19.80", exit_reason="stop")
    # Logged net liquidation 9000: the stop is -180; ORB (-37.50) and MOMO (-150.00) cross it at 10:50.
    report = check(await build_day(tmp_path, monkeypatch, Spec(trades=(ORB, losing, VWAP), net_liq="9000.00")))

    assert rule(report, 5).status == sc.FAIL, report.render()
    assert "VWAP_PB_V2 CCC (orders 16-18) sent after the realized loss had reached 2% of net liquidation" in \
        rule(report, 5).summary


async def test_an_entry_while_its_identity_was_fenced_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fenced = ("paper:ORB_V2", 1, "telegram /halt by owner", _utc("09:30:00").isoformat())
    report = check(await build_day(tmp_path, monkeypatch, Spec(fences=(fenced,))))

    assert rule(report, 6).status == sc.FAIL, report.render()
    assert "ORB_V2 BBB (orders 13-15) sent at 09:50:00 ET while paper:ORB_V2 was stopped" in rule(report, 6).summary


async def test_an_identity_fenced_during_the_day_is_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fenced = ("paper:GAP_GO_V2", 1, "sentinel: daily loss", _utc("12:00:00").isoformat())
    report = check(await build_day(tmp_path, monkeypatch, Spec(fences=(fenced,))))

    assert statuses(report) == {**ALL_PASS_BUT_SIZE, 6: sc.WARN}, report.render()
    assert "fenced during the day: paper:GAP_GO_V2: STOPPED" in rule(report, 6).summary
    assert report.exit_code == sc.EXIT_PASSED


# --- rule 7: the day risk budget (v7c) -------------------------------------------------------------

# An owner buy as bridge v7c places it (ManualDesk.buy: the budget line, then the buy with its stop),
# an owner buy and a bot entry the budget refused (the bridge's own texts, test_paper_bridge).
MANUAL_BUDGET = ("10:10:00", "INFO", budget_ok_line("AAPL", "manual #abc123", realized=Decimal("0"),
                                                    open_risk=Decimal("337.50"), new=Decimal("50.10"),
                                                    net_liq=NET_LIQ, source="manual"))
MANUAL_BUY = ("10:10:00", "MANUAL", "SENT BUY 10 AAPL MKT #abc123 (order 55) with its stop 185 GTC (order 56)")
MANUAL_REFUSED = ("10:12:00", "MANUAL", "REJECTED BUY 10 TSLA MKT #def456: v7c_day_risk_budget: realized 0.00 + open"
                  " 2990.00 + new 50.10 = 3040.10 (3.04%) > 3% of 100000.00 (source=manual). Nothing sent."
                  " (v7c_day_risk_budget)")
BOT_REFUSED = ("10:15:00", "SKIP", "ZZZ (MOMO_HOD_V2): v7c_day_risk_budget: realized 0.00 + open 2990.00 + new 50.00"
               " = 3040.00 (3.04%) > 3% of 100000.00 (source=bot) — would have sent BUY 100 @ 50.00 | stop 49.50 |"
               " target 51.00")
OWNER_DAY = (MANUAL_BUDGET, MANUAL_BUY, MANUAL_REFUSED, BOT_REFUSED)


def _edit_log(day: Day, old: str, new: str) -> None:
    log = day.logs / "bridge.log"
    text = log.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    log.write_text(text.replace(old, new), encoding="utf-8")


@pytest.mark.parametrize("case", ["pass", "over-3pct", "malformed", "no-lines-v7c", "no-lines-pre-v7c",
                                  "nothing-sent-pre-v7c", "other-cap"])
async def test_rule_day_risk_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    spec = {
        "pass": Spec(extra=OWNER_DAY),
        "over-3pct": Spec(budget_open=(("BBB", "2970.00"),)),  # ORB: 2970.00 + 37.50 = 3007.50
        "malformed": Spec(),
        "no-lines-v7c": Spec(budget_lines=False, extra=(MANUAL_BUY,)),
        "no-lines-pre-v7c": Spec(version="v7b"),
        "nothing-sent-pre-v7c": Spec(version="v7b", trades=(), flat=True),
        "other-cap": Spec(),
    }[case]
    day = await build_day(tmp_path, monkeypatch, spec)
    if case == "malformed":
        _edit_log(day, "AAA (MOMO_HOD_V2): realized", "AAA (MOMO_HOD_V2): realised")
    if case == "other-cap":
        _edit_log(day, "INFO day risk budget 3% of net liquidation", "INFO day risk budget 30% of net liquidation")
    report = check(day)
    result = rule(report, 7)
    text = "\n".join([result.summary, *result.details])

    assert result.title == "day risk budget"
    if case == "pass":
        assert statuses(report) == ALL_PASS_BUT_SIZE, report.render()
        assert result.summary.startswith(
            "5 accepted entries (4 bot, 1 manual) within 3% of net liquidation, highest 0.39% (owner buy #abc123"
            " (order 55)); refused: bot v7c_day_risk_budget x1, manual v7c_day_risk_budget x1"), result.summary
        assert "ORB_V2 BBB (orders 13-15): 0.19% of net liquidation 100000.00: bridge.log:" in text
        assert report.exit_code == sc.EXIT_PASSED
    elif case == "over-3pct":
        assert result.status == sc.FAIL and report.exit_code == sc.EXIT_FAILED
        assert ("accepted at a day risk of 3007.50 = 3.01% of net liquidation 100000.00, above 3%"
                in result.summary), result.summary
    elif case == "malformed":
        assert result.status == sc.FAIL
        assert result.summary.startswith("2 problem(s); first: malformed v7c_budget_ok line: bridge.log:")
        # Its SENT is not verified by any other line: a v7c process sent it without a readable one.
        assert "MOMO_HOD_V2 AAA (orders 16-18) sent without its v7c_budget_ok line" in text
    elif case == "no-lines-v7c":
        assert result.status == sc.FAIL
        assert "5 problem(s); first: GAP_GO_V2 DDD (orders 10-12) sent without its v7c_budget_ok line" in \
            result.summary
        assert "owner buy #abc123 (order 55) sent without its v7c_budget_ok line" in text
    elif case == "no-lines-pre-v7c":
        assert result.status == sc.WARN, report.render()
        assert result.summary.startswith(
            "day_risk_budget_unverified: 4 of 4 accepted entries without a v7c_budget_ok line"), result.summary
        assert report.exit_code == sc.EXIT_PASSED
    elif case == "nothing-sent-pre-v7c":
        assert (result.status, result.summary) == (sc.PASS, "no risk-increasing order sent; refused: none")
    else:
        assert result.status == sc.FAIL
        assert ("the bridge ran a day risk budget of 30% (stop slippage factor 1.0), the approved is 3% (stop"
                " slippage factor 1.0)") in result.summary


async def test_rule_day_risk_budget_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    over = ("12:00:00", "CRITICAL", "v7c_budget_over: realized 2000.00 + open 1100.00 + new 0.00 = 3100.00 (3.10%)"
            " > 3% of 100000.00 — no new entries; never flattened")
    widened = ("12:01:00", "CRITICAL", "v7c_stop_widened CCC intent 9.90 now 9.50 — counted at 9.50 in the day risk"
               " budget; never flattened")
    report = check(await build_day(tmp_path, monkeypatch, Spec(extra=(over, widened))))
    assert (rule(report, 7).status, report.exit_code) == (sc.WARN, sc.EXIT_PASSED), report.render()
    assert rule(report, 7).summary.startswith("2 flag(s); first: v7c_budget_over page: bridge.log:")

    broken = ("12:02:00", "CRITICAL", "v7c_budget_invariant_broken: open risk grew by 90.00 with CCC (VWAP_PB_V2),"
              " more than its own 75.00 (0.00 -> 90.00) — check TWS")
    report = check(await build_day(tmp_path / "broken", monkeypatch, Spec(extra=(over, broken))))
    assert rule(report, 7).status == sc.FAIL
    assert rule(report, 7).summary.startswith("1 problem(s); first: the bridge's budget tripwire fired:")


def _restart_before_sent(day: Day, symbol: str, identity: str, *, sha: bool = True,
                         rechecked: str | None = None) -> None:
    """A new bridge process between a SENT's budget line and the SENT (the first one stopped after its
    check): its sha line (a new run) unless ``sha`` is False, its connect and budget-rule lines, and
    ``rechecked``, the new process's own budget line for that entry, when it logged one."""
    log = day.logs / "bridge.log"
    lines = log.read_text(encoding="utf-8").splitlines()
    budget = next(index for index, text in enumerate(lines) if f"v7c_budget_ok {symbol} ({identity})" in text)
    sent = next(index for index, text in enumerate(lines) if f" SENT {symbol} ({identity})" in text)
    assert budget < sent
    clock = lines[sent].split(" ET")[0]
    restart = [
        *([_line(clock, "INFO", f"bridge sha256={BRIDGE_SHA}")] if sha else []),
        _line(clock, "INFO", f"PAPER bridge v7c connected (all accounts paper). Managing brackets for {DAY}"
              " until 15:55 ET."),
        _line(clock, "INFO", pb.budget_line()),
        *([_line(clock, "INFO", rechecked)] if rechecked is not None else []),
    ]
    lines[sent:sent] = restart
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("boundary", ["new-run", "second-connect"])
async def test_rule_day_risk_budget_never_pairs_a_sent_with_an_earlier_process_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """N18g-LOW-1 (probe Q1): a budget line logged by a process that stopped before its SENT never
    vouches for a later process's SENT; the later SENT without a line of its own FAILs."""
    day = await build_day(tmp_path, monkeypatch)
    single = check(day)  # the normal single-process day still passes
    assert rule(single, 7).status == sc.PASS and "1 bridge process(es)" in single.inputs[0]
    _restart_before_sent(day, "AAA", "MOMO_HOD_V2", sha=boundary == "new-run")
    report = check(day)
    result = rule(report, 7)

    # A sha line starts a new run of the section; a second connect inside one run is a new process too.
    assert f"{2 if boundary == 'new-run' else 1} bridge process(es)" in report.inputs[0]
    assert result.status == sc.FAIL, result.summary
    assert result.summary.startswith("1 problem(s); first: MOMO_HOD_V2 AAA (orders 16-18) sent without its"
                                     " v7c_budget_ok line, by a process that enforces the day risk budget"), \
        result.summary
    stale = [detail for detail in result.details if detail.startswith("budget checked, no order followed")]
    assert len(stale) == 1 and "v7c_budget_ok AAA (MOMO_HOD_V2)" in stale[0]


async def test_rule_day_risk_budget_pairs_a_restarted_process_with_its_own_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart that checks again (the bridge's only path: budget_check right before placeOrder) passes:
    the new process's line verifies its SENT; the first process's line is listed, never paired."""
    day = await build_day(tmp_path, monkeypatch)
    again = budget_ok_line("AAA", "MOMO_HOD_V2", realized=Decimal("0"), open_risk=Decimal("0"),
                           new=Decimal("150.00"), net_liq=NET_LIQ)
    _restart_before_sent(day, "AAA", "MOMO_HOD_V2", rechecked=again)
    result = rule(check(day), 7)

    assert result.status == sc.PASS, result.summary
    assert result.summary.startswith("4 accepted entries (4 bot, 0 manual) within 3% of net liquidation")
    assert "MOMO_HOD_V2 AAA (orders 16-18): 0.15% of net liquidation 100000.00" in "\n".join(result.details)
    assert sum(detail.startswith("budget checked, no order followed") for detail in result.details) == 1


def _logline(text: str) -> sc.LogLine:
    return sc.LogLine("bridge.log", 7, 0, time(10, 0), "INFO", text)


@pytest.mark.parametrize(("total", "problem"), [
    ("3000.00", False), ("3000.01", False), ("3000.02", True),  # one cent of the bridge's rounding, no more
])
def test_a_budget_line_at_the_cap_passes_and_one_cent_past_its_rounding_fails(total: str, problem: bool) -> None:
    new = Decimal(total) - Decimal("2000.00")
    check_ = sc.parse_budget_ok(_logline(budget_ok_line("AAA", "ORB_V2", realized=Decimal("0"),
                                                        open_risk=Decimal("2000.00"), new=new, net_liq=NET_LIQ)))
    assert isinstance(check_, sc.BudgetCheck)
    assert check_.key == ("bot", "AAA", "ORB_V2") and check_.total == Decimal(total)
    assert bool(check_.problems) is problem
    manual = sc.parse_budget_ok(_logline(budget_ok_line("BF B", "manual #a3f9c2", realized=Decimal("1.00"),
                                                       open_risk=Decimal("2.00"), new=Decimal("3.00"),
                                                       net_liq=NET_LIQ, source="manual")))
    assert isinstance(manual, sc.BudgetCheck) and manual.key == ("manual", "a3f9c2") and not manual.problems
    for broken in (
        budget_ok_line("AAA", "manual #a3f9c2", realized=Decimal("0"), open_risk=Decimal("0"), new=Decimal("1"),
                       net_liq=NET_LIQ),  # a manual label with source=bot
        "v7c_budget_ok AAA (ORB_V2): realized 0.00 + open 0.00 + new 1.00 = 9.00 (0.01%) <= 3% of 100000.00"
        " (source=bot)",  # parts that do not sum to the total
        "v7c_budget_ok AAA (ORB_V2): realized -5.00 + open 0.00 + new 1.00 = -4.00 (0.00%) <= 3% of 100000.00"
        " (source=bot)",  # a negative amount
    ):
        assert str(sc.parse_budget_ok(_logline(broken))).startswith("malformed v7c_budget_ok line")


# --- masking, read-only access, missing inputs, pins -----------------------------------------------------


def test_mask_accounts() -> None:
    assert sc.mask_accounts(f"for {FAKE_ACCOUNT} and DUABC12 but not DU123") == \
        "for DU1234567 and DU1234567 but not DU123"


async def test_a_real_format_account_id_never_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.cli import main

    fence = ("paper:ORB_V1", 1, f"owner stop for {FAKE_ACCOUNT}", _utc("09:00:00").isoformat())
    day = await build_day(tmp_path, monkeypatch, Spec(
        failed_first_process=True, fences=(fence,), data_name=f"{FAKE_ACCOUNT}-data",
        extra=(("15:46:00", "CRITICAL", f"CCC: still open after flatten (1 sh) — act in TWS {FAKE_ACCOUNT}"),),
    ))
    assert FAKE_ACCOUNT in (day.logs / "bridge.log").read_text(encoding="utf-8")  # the input does carry it

    text = check(day).render()
    code = main(["session", "check", "--date", str(DAY), "--logs", str(day.logs), "--data", str(day.data)])
    printed = capsys.readouterr().out

    assert code == sc.EXIT_FAILED
    for output in (text, printed):
        assert FAKE_ACCOUNT not in output
        assert "DU1234567" in output
    missing = sc.check_session(DAY, logs=day.logs, data=tmp_path / f"none-{FAKE_ACCOUNT}", db=day.db).render()
    assert FAKE_ACCOUNT not in missing and "INPUTS MISSING" in missing


def _snapshot(paths: list[Path]) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in paths}


async def test_the_database_is_opened_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    day = await build_day(tmp_path, monkeypatch, Spec(fences=(("paper:ORB_V1", 0, "resumed", "2026-10-01T00:00:00+00:00"),)))
    with closing(sqlite3.connect(day.db)) as writer:
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    inputs = sorted(path for path in tmp_path.rglob("*") if path.is_file() and not path.name.endswith(("-wal", "-shm")))
    before = _snapshot(inputs)

    connection = sc.open_read_only(day.db)
    try:
        for statement in ("INSERT INTO autotrader_execution_fences VALUES ('paper', 1, 'x', 'y')",
                          "DELETE FROM autotrader_execution_fences", "CREATE TABLE intruder (a)"):
            with pytest.raises(sqlite3.OperationalError, match="readonly|read-only|query_only"):
                connection.execute(statement)
    finally:
        connection.close()

    attempts: list[BaseException] = []
    original = sc.read_fences

    def read_fences_then_try_a_write(connection: sqlite3.Connection) -> list[sc.FenceRecord]:
        try:  # the checker's own connection, mid-check
            connection.execute("UPDATE autotrader_execution_fences SET stopped = 1")
        except sqlite3.OperationalError as exc:
            attempts.append(exc)
        return original(connection)

    monkeypatch.setattr(sc, "read_fences", read_fences_then_try_a_write)
    report = check(day)

    assert len(attempts) == 1 and "readonly" in str(attempts[0])
    assert report.exit_code == sc.EXIT_PASSED
    assert _snapshot(inputs) == before  # logs, state and database untouched
    with closing(sqlite3.connect(f"file:{day.db}?mode=ro", uri=True)) as reader:
        assert reader.execute("SELECT stopped FROM autotrader_execution_fences").fetchall() == [(0,)]


async def test_missing_inputs_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    day = await build_day(tmp_path, monkeypatch)

    no_state = sc.check_session(DAY, logs=day.logs, data=tmp_path / "empty", db=day.db)
    no_section = sc.check_session(date(2026, 10, 20), logs=day.logs, data=day.data, db=day.db)
    no_database = sc.check_session(DAY, logs=day.logs, data=day.data, db=tmp_path / "missing.db")
    closed_day = sc.check_session(date(2026, 10, 17), logs=day.logs, data=day.data, db=day.db)
    (day.logs / "supervisor.log").unlink()
    (day.logs / f"supervisor.log.{DAY}").unlink()
    no_supervisor = check(day)

    for report, problem in (
        (no_state, "no bridge state bridge/2026-10-19.json"),
        (no_section, "no bridge.log section for 2026-10-20"),
        (no_database, "no database at"),
        (closed_day, "2026-10-17 is not an XNYS session"),
        (no_supervisor, "no supervisor.log in"),
    ):
        assert report.exit_code == sc.EXIT_INPUTS_MISSING
        assert report.rules == []
        assert problem in report.render()
        assert report.render().startswith(f"Session check {report.session_date}: INPUTS MISSING (exit 2)")
    assert not (tmp_path / "missing.db").exists()  # a read-only open never creates a database


def test_rotation_carried_supervisor_lines_are_counted_once(tmp_path: Path) -> None:
    _write_logs(tmp_path / "logs", Spec())
    events, files = sc.read_supervisor_bridge(tmp_path / "logs", DAY)

    assert files == 2
    assert [(event.event, event.code) for event in events] == [("CHILD_START", None), ("CHILD_EXIT", "0")]


# --- pinned to the bridge -----------------------------------------------------------------------


def test_the_checker_mirrors_the_pinned_bridge() -> None:
    from trading.risk.identity_sizing import IDENTITY_SIZE_MULTIPLIER
    from trading.sendable_identities import SENDABLE_IDENTITIES

    assert {
        name: (caps.max_positions, caps.max_entries_per_day, caps.last_entry_before_close)
        for name, caps in pb.IDENTITY_CAPS.items()
    } == {
        name: (caps.max_positions, caps.max_entries_per_day, caps.last_entry_before_close)
        for name, caps in sc.BRIDGE_IDENTITY_CAPS.items()
    }
    assert set(sc.BRIDGE_IDENTITY_CAPS) == set(pb.SENDABLE_STRATEGY_NAMES) == set(SENDABLE_IDENTITIES)
    assert set(IDENTITY_SIZE_MULTIPLIER) == set(SENDABLE_IDENTITIES)
    assert sc.DECISIONS_SQL == pb.DECISIONS_SQL
    assert sc.SHADOW_ONLY_REASON == pb.SHADOW_ONLY
    assert (sc.SESSION_LAST_ENTRY_BEFORE_CLOSE, sc.FLATTEN_BEFORE_CLOSE) == (
        pb.LAST_ENTRY_BEFORE_CLOSE, pb.FLATTEN_BEFORE_CLOSE)
    assert (sc.MAX_DAILY_LOSS_PCT, sc.MAX_ORDER_NOTIONAL_PCT, sc.MAX_ORDER_RISK_PCT) == (
        pb.MAX_DAILY_LOSS_PCT, pb.MAX_ORDER_NOTIONAL_PCT, pb.MAX_ORDER_RISK_PCT)
    # Rule 7 (v7c): the cap and the factor the checker approves are the pinned bridge's.
    assert (sc.DAY_RISK_BUDGET_PCT, sc.STOP_SLIPPAGE_FACTOR) == (pb.DAY_RISK_BUDGET_PCT, pb.STOP_SLIPPAGE_FACTOR)
    assert sc._BUDGET_RULE.match(pb.budget_line()) is not None


async def test_the_checker_reads_decisions_exactly_as_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = await build_day(tmp_path, monkeypatch)
    rows = pb.accepted_decisions(day.db, DAY)
    with closing(sqlite3.connect(f"file:{day.db}?mode=ro", uri=True)) as connection:
        decisions = sc.read_decisions(connection, DAY)

    assert [row.decision_id for row in rows] == list(decisions)
    for row in rows:
        mine, theirs = decisions[row.decision_id], pb.parse_intent(row.payload_json)
        assert mine.strategy_name == row.strategy_name
        assert mine.intent is not None and theirs is not None
        assert (mine.intent.quantity, mine.intent.entry, mine.intent.stop, mine.intent.target) == (
            theirs.quantity, theirs.entry, theirs.stop, theirs.target)
