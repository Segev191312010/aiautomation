"""Hardening contract for the temporary PAPER bridge (backend/scripts_local/paper_bridge.py).

The bridge stays outside the trading package until the C2 execution-safety
review, so it is loaded from its path. TWS is never contacted: a synchronous
fake broker drives the real IBKR callbacks.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import closing
import copy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import importlib.util
import itertools
import json
from pathlib import Path
import re
import signal
import sqlite3
import stat
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

BRIDGE_PATH = Path(__file__).resolve().parents[1] / "scripts_local" / "paper_bridge.py"
if not BRIDGE_PATH.exists():
    pytest.skip("the temporary PAPER bridge script is not in this checkout", allow_module_level=True)


def _load_bridge() -> ModuleType:
    spec = importlib.util.spec_from_file_location("paper_bridge_under_test", BRIDGE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


pb = _load_bridge()

SESSION = date(2026, 10, 13)
ACCOUNT = "DU1234567"
CLIENT_ID = 18
SETUP = "a" * 64
EVENT_LINE = re.compile(r"^\d{2}:\d{2}:\d{2} ET  (?P<kind>[A-Z]+) (?P<text>\S.*)$")


def ny(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 13, hour, minute, second, tzinfo=pb.NY)


DECIDED = ny(10, 0)


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class AutoClock(Clock):
    """Advances on every read, so a loop with no sleep still reaches its deadline."""

    def __init__(self, value: datetime, step_seconds: float) -> None:
        super().__init__(value)
        self.step = timedelta(seconds=step_seconds)

    def __call__(self) -> datetime:
        current = self.value
        self.value += self.step
        return current


class SteppingStop:
    """Stop-event stand-in: every loop wait moves the fake clock forward."""

    def __init__(self, clock: Clock, seconds: float) -> None:
        self.clock = clock
        self.seconds = seconds
        self.flag = False

    def is_set(self) -> bool:
        return self.flag

    def set(self) -> None:
        self.flag = True

    def wait(self, timeout: float | None = None) -> bool:
        self.clock.advance(self.seconds)
        return self.flag


class FakeBroker(pb.BrokerState):
    """Synchronous IBKR double: every request answers through the real callbacks."""

    def __init__(
        self,
        *,
        accounts: str = ACCOUNT,
        bid: Decimal | None = None,
        live_orders: dict[int, str] | None = None,
        executions: tuple[SimpleNamespace, ...] = (),
        answer: bool = True,
        positions: dict[int, int] | None = None,
        order_objects: dict[int, Any] | None = None,
        ask: Decimal | None = None,
        contracts: dict[int, Any] | None = None,
    ) -> None:
        super().__init__(CLIENT_ID)
        self.accounts_reply = accounts
        self.quote = bid
        self.live_orders = dict(live_orders or {})
        self.broker_executions = list(executions)
        self.answer = answer
        self.account_positions = dict(positions or {})  # conId -> shares the account holds
        self.reported_orders = dict(order_objects or {})  # orderId -> Order as TWS reports it
        self.echo_orders = True  # TWS reports every accepted order or modification through openOrder
        self.ask = ask
        self.position_contracts_reply = dict(contracts or {})  # conId -> contract reqPositions reports
        self.book: list[tuple[int, Any, Any, str, float]] = []  # every client's orders for reqAllOpenOrders
        self.open_order_contracts: dict[int, Any] = {}  # orderId -> contract reqOpenOrders reports
        self.completed: list[tuple[Any, Any, Any]] = []  # reqCompletedOrders
        self.connected = False
        self.connected_checks = 0
        self.disconnect_after: int | None = None
        self.on_connected_check: Callable[[int], None] | None = None
        self.placed: list[tuple[int, Any]] = []
        self.cancelled: list[int] = []
        self.pnl_requests: list[tuple[int, str, str]] = []
        self.own_orders: dict[int, Any] = {}  # orderId -> the latest version this client placed
        # v7c: TWS also reports this client's own working orders to reqAllOpenOrders; the older
        # tests predate that and keep the book to other clients' orders only.
        self.own_in_all_open_orders = False
        # v7c fix 1 (N18 N21): TWS reports a new order (openOrder, orderStatus) a moment after
        # placeOrder; with delay_echoes the reports wait for deliver_echoes().
        self.delay_echoes = False
        self.delayed_echoes: list[Callable[[], None]] = []

    def connect(self, host: str, port: int, client_id: int) -> None:
        assert (host, port, client_id) == ("127.0.0.1", 7497, CLIENT_ID)
        self.connected = True
        if self.answer:
            self.managedAccounts(self.accounts_reply)
            self.nextValidId(100)

    def run(self) -> None:
        return None

    def isConnected(self) -> bool:
        self.connected_checks += 1
        if self.on_connected_check is not None:
            self.on_connected_check(self.connected_checks)
        if self.disconnect_after is not None and self.connected_checks > self.disconnect_after:
            self.connected = False
        return self.connected

    def disconnect(self) -> None:
        self.connected = False

    def reqAccountSummary(self, request_id: int, group: str, tags: str) -> None:
        self.accountSummary(request_id, ACCOUNT, "NetLiquidation", "665000.00", "USD")
        self.accountSummaryEnd(request_id)

    def cancelAccountSummary(self, request_id: int) -> None:
        return None

    def reqPnL(self, request_id: int, account: str, model_code: str) -> None:
        self.pnl_requests.append((request_id, account, model_code))  # v7c: a test answers through pnl()

    def reqMarketDataType(self, kind: int) -> None:
        return None

    def reqOpenOrders(self) -> None:
        for order_id, status in self.live_orders.items():
            contract = self.open_order_contracts.get(order_id, SimpleNamespace(symbol=f"SYM{order_id}"))
            self.openOrder(order_id, contract, self.as_reported(order_id), SimpleNamespace(status=status))
        self.openOrderEnd()

    def as_reported(self, order_id: int) -> Any:
        reported = self.reported_orders.get(order_id)
        if reported is None:
            return None
        echo = copy.copy(reported)
        echo.clientId = CLIENT_ID  # TWS reports this client's id on its own orders
        return echo

    def reqExecutions(self, request_id: int, execution_filter: Any) -> None:
        assert execution_filter.clientId == CLIENT_ID
        for item in self.broker_executions:
            self.execDetails(request_id, None, item)
        self.execDetailsEnd(request_id)

    def reqPositions(self) -> None:
        for con_id, shares in self.account_positions.items():
            contract = self.position_contracts_reply.get(con_id, SimpleNamespace(conId=con_id))
            self.position(ACCOUNT, contract, float(shares), 50.0)
        self.positionEnd()

    def reqAllOpenOrders(self) -> None:
        for order_id, contract, order, status, remaining in self.book:
            self.openOrder(order_id, contract, order, SimpleNamespace(status=status))
            self.orderStatus(order_id, status, 0, remaining, 0, order.permId, 0, 0, order.clientId, "", 0)
        if self.own_in_all_open_orders:
            own = dict(self.own_orders)
            own.update(self.reported_orders)  # what TWS shows now: an edit made in TWS included
            for order_id, shown in sorted(own.items()):
                status = self.order_status.get(order_id) or self.live_orders.get(order_id)
                if status is None or status in pb.DEAD_ORDER_STATUSES:
                    continue
                echo = copy.copy(shown)
                echo.clientId = CLIENT_ID
                row = self.open_orders.get(order_id)
                contract = self.open_order_contracts.get(order_id) or SimpleNamespace(
                    symbol=row.symbol if row else "", conId=row.con_id if row else 0)
                self.openOrder(order_id, contract, echo, SimpleNamespace(status=status))
        self.openOrderEnd()

    def reqCompletedOrders(self, api_only: bool) -> None:
        for contract, order, state in self.completed:
            self.completedOrder(contract, order, state)
        self.completedOrdersEnd()

    def reqMktData(self, request_id: int, contract: Any, *args: Any) -> None:
        if self.quote is not None:
            self.tickPrice(request_id, 1, float(self.quote), None)
        if self.ask is not None:
            self.tickPrice(request_id, 2, float(self.ask), None)

    def cancelMktData(self, request_id: int) -> None:
        return None

    def placeOrder(self, order_id: int, contract: Any, order: Any) -> None:
        self.placed.append((order_id, order))
        self.own_orders[order_id] = order
        status = self.order_status.get(order_id, "PreSubmitted" if order.orderType == "STP" else "Submitted")

        def report() -> None:
            if self.echo_orders:
                echo = copy.copy(order)
                echo.clientId = CLIENT_ID
                self.openOrder(order_id, SimpleNamespace(symbol="", conId=contract.conId), echo,
                               SimpleNamespace(status=status))
            self.orderStatus(order_id, status, 0, order.totalQuantity, 0)

        if self.delay_echoes:
            self.delayed_echoes.append(report)
        else:
            report()

    def deliver_echoes(self) -> None:
        reports, self.delayed_echoes = self.delayed_echoes, []
        for report in reports:
            report()

    def cancelOrder(self, order_id: int) -> None:
        self.cancelled.append(order_id)


def connected_broker(**kwargs: Any) -> FakeBroker:
    broker = FakeBroker(**kwargs)
    broker.connect("127.0.0.1", 7497, CLIENT_ID)
    broker.reqAccountSummary(1, "All", "NetLiquidation")
    broker.reqPositions()
    return broker


def execution(exec_id: str, order_id: int, shares: Any, price: Any, client_id: int = CLIENT_ID,
              **fields: Any) -> SimpleNamespace:
    """An ibapi Execution; ``fields`` adds acctNumber, orderRef or time as TWS reports them."""
    return SimpleNamespace(execId=exec_id, orderId=order_id, shares=shares, price=price, clientId=client_id,
                           **fields)


def critical(out: list[tuple[str, str]]) -> list[str]:
    return [text for kind, text in out if kind == "CRITICAL"]


def bracket(decision_id: str, symbol: str, *, status: str, parent: int, filled: int = 0,
            exit_filled: int = 0, qty: int = 100, **fields: Any) -> Any:
    return pb.Bracket(
        decision_id=decision_id, symbol=symbol, con_id=parent * 10, primary_exchange="NASDAQ",
        strategy="momo", strategy_name="MOMO_HOD_V2", qty=qty, entry="50.00", stop="49.50",
        target="51.00", parent=parent, target_id=parent + 1, stop_id=parent + 2,
        sent_at=DECIDED.astimezone(timezone.utc).isoformat(), status=status, filled=filled,
        avg_entry="50.0000" if filled else "0", exit_filled=exit_filled, **fields,
    )


def decision_row(decision_id: str, symbol: str, *, strategy: str = "MOMO_HOD_V2",
                 created: datetime = DECIDED, con_id: int = 4931, **intent_fields: Any) -> Any:
    intent = {"quantity": 100, "entry_limit": "50.00", "stop_price": "49.50", "target_price": "51.00",
              **intent_fields}
    return pb.DecisionRow(
        decision_id=decision_id, run_id=f"shadow-x-{SESSION}", symbol=symbol, con_id=con_id,
        contract_json=json.dumps({"primary_exchange": "NYSE"}),
        payload_json=json.dumps({"risk": {"intent": intent}}),
        created_at=created.astimezone(timezone.utc).isoformat(), strategy_name=strategy,
    )


def make_bridge(tmp_path: Path, broker: FakeBroker, *, brackets: dict[str, Any] | None = None,
                clock: Clock | None = None, decisions: tuple[Any, ...] = (),
                db: Path | None = None, session_date: date = SESSION) -> Any:
    store = pb.StateStore(state_dir=tmp_path / "bridge", legacy_path=tmp_path / "paper_bridge_state.json",
                          session_date=session_date)
    bridge = pb.Bridge(broker, ACCOUNT, session_date=session_date, store=store, brackets=dict(brackets or {}),
                       db=db or tmp_path / "absent.db", clock=clock or Clock(DECIDED + timedelta(seconds=5)))
    bridge.quote_timeout = 0.01
    bridge.sync_timeout = 0.5
    bridge.loop_seconds = 0
    if db is None:  # no ledger: the test supplies decisions, a clear fence and no snapshot
        bridge.decisions = lambda: list(decisions)
        bridge.kill_switch_active = lambda: False
        bridge.identity_fence_active = lambda identity: False
        bridge.fallback_quote = lambda item: None
    return bridge


def events(capsys: pytest.CaptureFixture[str]) -> list[tuple[str, str]]:
    """Every stdout line must be a notifier event line with a known KIND."""
    parsed: list[tuple[str, str]] = []
    for line in capsys.readouterr().out.splitlines():
        match = EVENT_LINE.match(line)
        assert match is not None, f"not a notifier event line: {line!r}"
        assert match["kind"] in pb.EVENT_KINDS, line
        parsed.append((match["kind"], match["text"]))
    return parsed


# ---- ledger fixtures -------------------------------------------------------------


async def _shadow_strategy(name: str) -> str:
    from tests.fakes.legacy_strategies import insert_legacy_shadow_strategy
    from trading.contracts import FixedUniverse, RuleCondition, StrategySpec, StrategyState
    from trading.sendable_identities import carries_sendable_prefix
    from trading.storage.repository import create_strategy, transition_strategy

    spec = StrategySpec(
        name=name,
        universe=FixedUniverse(symbols=("AAPL",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale=f"{name} bridge fixture.",
    )
    if carries_sendable_prefix(name):
        # The name guard refuses to create a sendable name outside the real registry
        # flow; the fixture inserts it the way the live ledger holds it.
        return (await insert_legacy_shadow_strategy(spec=spec, owner_id="demo")).strategy_id
    record = await create_strategy(spec=spec, owner_id="demo", actor="demo")
    for revision, target in enumerate((StrategyState.BACKTESTING, StrategyState.SHADOW)):
        record = await transition_strategy(
            strategy_id=record.strategy_id, version=1, owner_id="demo", expected_revision=revision,
            target=target, actor="demo", reason="fixture",
        )
    return record.strategy_id


async def _run(run_id: str, strategy_id: str, day: date) -> str:
    from trading.contracts import EngineMode, RiskPolicy, Run, ScannerRun, fingerprint_payload
    from trading.data import RunRepository, ScannerRunRepository

    await RunRepository().save(Run(
        id=run_id, owner_id="demo", source_sha="abcdef1", strategy_id=strategy_id, strategy_version=1,
        policy_fingerprints={"risk": RiskPolicy().fingerprint()}, session_date=day, mode=EngineMode.SHADOW,
    ))
    parameters = {"scan_code": "HOT_BY_VOLUME", "limit": 50}
    await ScannerRunRepository().save(ScannerRun(
        id=f"{run_id}-scan", run_id=run_id, parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters), status="completed",
        started_at=datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc),
    ))
    return run_id


async def _accept(run_id: str, strategy_id: str, symbol: str, created: datetime) -> str:
    from trading.contracts import (
        AIReview, Candidate, Decision, DecisionOutcome, RiskApproval, TradeIntent,
    )
    from trading.data import CandidateRepository
    from trading.storage.repository import record_decision

    candidate = Candidate(
        id=f"{run_id}-{symbol}", scanner_run_id=f"{run_id}-scan", con_id=4931 if " " in symbol else 265598,
        symbol=symbol, primary_exchange="NYSE" if " " in symbol else "NASDAQ", rank=1,
        admission_status="admitted",
    )
    await CandidateRepository().save(candidate)
    review = AIReview(
        setup_fingerprint=SETUP, decision="PROPOSE", confidence="0.7", rationale="Fixture breakout.",
        model_id="fixture-model", prompt_version="fixture-prompt", response_hash="c" * 64,
        latency_ms=5, cost_usd="0.01",
    )
    expires = created + timedelta(seconds=15)
    intent = TradeIntent(
        execution_key=f"exec-{run_id}-{symbol}", setup_fingerprint=SETUP, approval_token="t" * 32,
        expires_at=expires, strategy_id=strategy_id, strategy_version=1, symbol=symbol, quantity=100,
        entry_limit="50.00", stop_price="49.50", target_price="51.00",
    )
    decision = Decision(
        id=f"decision-{run_id}-{symbol}", run_id=run_id, candidate_id=candidate.id,
        strategy_id=strategy_id, strategy_version=1,
        decision_key=sha256(f"{run_id}:{symbol}".encode()).hexdigest(),
        outcome=DecisionOutcome.SHADOW_ACCEPTED, setup_fingerprint=SETUP, ai_review=review,
        risk=RiskApproval(
            approved=True, intent=intent, setup_fingerprint=SETUP,
            ai_review_fingerprint=review.fingerprint(), policy_fingerprint="d" * 64,
            portfolio_fingerprint="e" * 64, expires_at=expires,
        ),
        created_at=created,
    )
    await record_decision(decision=decision, owner_id="demo")
    return decision.id


async def _ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from config import cfg
    from db.core import init_db

    db_path = tmp_path / "ledger.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(db_path))
    await init_db()
    return db_path


async def seed_decisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    db_path = await _ledger(tmp_path, monkeypatch)
    momo = await _shadow_strategy("MOMO_HOD_V2")
    orb = await _shadow_strategy("ORB_V1")
    # The run ids lie about their strategy on purpose: a label must never decide.
    momo_run = await _run("shadow-orb-lookalike-2026-10-13", momo, SESSION)
    orb_run = await _run("shadow-momo-lookalike-2026-10-13", orb, SESSION)
    old_run = await _run("shadow-momo-2026-10-12", momo, date(2026, 10, 12))
    utc = DECIDED.astimezone(timezone.utc)
    ids = {
        "momo": await _accept(momo_run, momo, "AAPL", utc),
        "orb": await _accept(orb_run, orb, "MMED", utc + timedelta(seconds=1)),
        "yesterday": await _accept(old_run, momo, "BF B", utc - timedelta(days=1)),
    }
    return db_path, ids


# ---- state -------------------------------------------------------------------------


def test_state_is_per_date_private_and_mirrored_for_the_dashboard(tmp_path: Path) -> None:
    from routers.live_routes import parse_bridge_state

    state_dir = tmp_path / "bridge"
    legacy = tmp_path / "paper_bridge_state.json"
    # Yesterday's BF B, exactly as bridge v2 left it in the shared legacy file.
    yesterday = bracket("bf", "BF B", status="closed", parent=2, filled=3741, exit_filled=3741, qty=3741)
    legacy.write_text(json.dumps({"submitted": {"bf": pb.asdict(yesterday)}}), encoding="utf-8")

    store = pb.StateStore(state_dir=state_dir, legacy_path=legacy, session_date=SESSION)
    assert store.load() == {}  # the legacy file is a mirror, never an input

    held = bracket("d1", "AAPL", status="open", parent=30, filled=100)
    store.save({"d1": held})
    path = state_dir / "2026-10-13.json"
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o600
    assert legacy.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["session_date"] == "2026-10-13"
    entry = document["submitted"]["d1"]
    assert (entry["parent"], entry["symbol"], entry["skipped"], entry["status"]) == (30, "AAPL", False, "open")
    assert store.load() == {"d1": held}

    # The dashboard reader keeps working on the mirror.
    [view] = parse_bridge_state(legacy.read_text(encoding="utf-8"))
    assert (view.symbol, view.status, view.strategy, view.parent_order_id) == ("AAPL", "open", "momo", 30)

    tomorrow = pb.StateStore(state_dir=state_dir, legacy_path=legacy, session_date=date(2026, 10, 14))
    assert tomorrow.load() == {}
    (state_dir / "2026-10-14.json").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(pb.StateError, match="holds session 2026-10-13"):
        tomorrow.load()
    assert sorted(item.name for item in state_dir.iterdir()) == ["2026-10-13.json", "2026-10-14.json"]


# ---- strategy authority -----------------------------------------------------------------


@pytest.mark.anyio
async def test_strategy_comes_from_the_ledger_never_the_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path, ids = await seed_decisions(tmp_path, monkeypatch)
    rows = pb.accepted_decisions(db_path, SESSION)
    assert [(row.symbol, row.strategy_name) for row in rows] == [("AAPL", "MOMO_HOD_V2"), ("MMED", "ORB_V1")]

    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, db=db_path)
    capsys.readouterr()
    bridge.tick()
    bridge.tick()

    out = events(capsys)
    assert [item for item in out if item[0] == "SKIP"] == [("SKIP", "MMED (ORB_V1): shadow-only strategy")]
    assert [item for item in out if item[0] == "SENT"] == [
        ("SENT", "AAPL (MOMO_HOD_V2): BUY 100 @ 50.00 | stop 49.50 | target 51.00 (orders 100-102)")
    ]
    assert [order_id for order_id, _ in broker.placed] == [100, 101, 102]
    momo, orb = bridge.brackets[ids["momo"]], bridge.brackets[ids["orb"]]
    assert (momo.status, momo.strategy, momo.strategy_name) == ("sent", "momo", "MOMO_HOD_V2")
    assert (orb.status, orb.strategy, orb.strategy_name, orb.skip_reason) == (
        "skipped", "orb", "ORB_V1", "shadow-only strategy"
    )
    assert (orb.qty, orb.entry, orb.parent) == (100, "50.00", None)  # shown, never sent
    assert ids["yesterday"] not in bridge.brackets


# ---- fills -------------------------------------------------------------------------------


def test_fills_never_decrease_and_missing_executions_never_divide_by_zero(tmp_path: Path) -> None:
    broker = connected_broker()
    # v2 crashed here: yesterday's closed BF B with no executions today gave 0/0.
    closed = bracket("bf", "BF B", status="closed", parent=2, filled=3741, exit_filled=3741, qty=3741,
                     avg_exit="26.6627", realized_pnl="-325.09", exit_reason="stop")
    held = bracket("held", "HELD", status="open", parent=10, filled=500, qty=600)
    bridge = make_bridge(tmp_path, broker, brackets={"bf": closed, "held": held})

    bridge.refresh(closed)
    assert (closed.filled, closed.exit_filled, closed.realized_pnl, closed.status) == (3741, 3741, "-325.09", "closed")
    assert broker.fills_for(3) == (0, Decimal("0"))
    assert broker.fills_for(None) == (0, Decimal("0"))
    assert pb.average(Decimal("0"), 0) == Decimal("0")
    broker.execDetails(-1, None, execution("zero", 3, 0, 26.6))
    assert broker.fills_for(3) == (0, Decimal("0"))

    # After a TWS restart IBKR reports only part of the entry: the count holds.
    broker.execDetails(-1, None, execution("a", 10, 200, 10.0))
    broker.execDetails(-1, None, execution("manual", 10, 999, 1.0, client_id=0))  # owner's TWS trade
    bridge.refresh(held)
    assert (held.filled, held.avg_entry) == (500, "50.0000")
    broker.execDetails(-1, None, execution("b", 10, 400, 10.1))
    bridge.refresh(held)
    assert (held.filled, held.avg_entry) == (600, "10.0667")


# ---- reconciliation -------------------------------------------------------------------------


def test_startup_reconciliation_flags_brackets_ibkr_does_not_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The slot mechanism under the v5 global cap of 3 (v7b's shipped sum is 6).
    monkeypatch.setattr(pb, "IDENTITY_CAPS", {"MOMO_HOD_V2": pb.IDENTITY_CAPS["MOMO_HOD_V2"]})
    ghost = bracket("ghost", "GHST", status="sent", parent=20)
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    working = bracket("work", "WORK", status="sent", parent=40)
    done = bracket("done", "DONE", status="closed", parent=50, filled=100, exit_filled=100)
    broker = connected_broker(
        bid=Decimal("50.10"),
        live_orders={31: "Submitted", 32: "PreSubmitted", 40: "Submitted", 52: "PreSubmitted",
                     99: "Submitted"},
        executions=(execution("e1", 30, 100, 50.0), execution("e2", 21, 7, 50.0, client_id=0)),
        positions={300: 100},
    )
    decisions = (
        decision_row("ghost", "GHST"),  # already known: never re-sent
        decision_row("new", "NEWX"),
        decision_row("again", "GHST"),  # an unreconciled symbol stays blocked
        decision_row("foreign", "SYM99"),  # so does a symbol with an order the bridge does not manage
    )
    bridge = make_bridge(tmp_path, broker, decisions=decisions,
                         brackets={"ghost": ghost, "held": held, "work": working, "done": done})
    capsys.readouterr()
    bridge.startup()

    assert (ghost.status, held.status, working.status, done.status) == (
        "needs_attention", "open", "sent", "closed"
    )
    out = events(capsys)
    assert critical(out) == [
        "GHST: bracket not found at IBKR — check TWS",
        "DONE: order 52 is still live at IBKR for a closed bracket — check TWS",
        "order 99 (SYM99) is live at IBKR but not in the 2026-10-13 bridge state — SYM99 blocked for new"
        " entries; check TWS",
    ]
    saved = json.loads((tmp_path / "bridge" / "2026-10-13.json").read_text(encoding="utf-8"))
    assert saved["submitted"]["ghost"]["status"] == "needs_attention"
    assert {99, 20, 21, 22} <= broker.own_order_ids  # their rejections are CRITICAL too

    bridge.tick()
    # L4: DONE's dangling stop and SYM99 hold one slot each next to HELD and WORK, so nothing new is sent.
    assert broker.placed == []
    assert bridge.brackets["new"].skip_reason == "max 3 positions/entries already active"
    assert bridge.brackets["again"].skip_reason == "symbol already has an active bracket"
    assert bridge.brackets["foreign"].skip_reason == "symbol has a live order at IBKR the bridge does not manage"
    assert ghost.status == "needs_attention" and ghost.parent == 20


def test_resize_after_partial_cancel_never_exceeds_the_shares_held(tmp_path: Path) -> None:
    held = bracket("held", "HELD", status="open", parent=10, filled=40, parent_cancel_requested=True)
    broker = connected_broker(executions=(execution("p", 10, 40, 50.0), execution("t", 11, 10, 51.0)),
                              positions={100: 30})
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    for order_id, status in ((10, "Cancelled"), (11, "Submitted"), (12, "PreSubmitted")):
        broker.orderStatus(order_id, status, 0, 0, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"held": held})
    bridge.refresh(held)
    bridge.resize_children(held)
    # The target already sold 10, so both legs may only cover the 30 shares still held.
    assert [(oid, o.orderType, o.totalQuantity, o.transmit) for oid, o in broker.placed] == [
        (11, "LMT", 40, True), (12, "STP", 30, True),
    ]
    assert held.resized_qty == 40


# ---- flatten ---------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flatten_without_a_live_bid_prices_from_the_newest_screener_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from db.core import get_db

    db_path = await _ledger(tmp_path, monkeypatch)
    async with get_db() as db:
        for snapshot_id, day, observed, bid, last in (
            ("older", "2026-10-13", "2026-10-13T19:30:00+00:00", "51.90", "52.00"),
            ("newest", "2026-10-13", "2026-10-13T19:44:00+00:00", "50.30", "50.40"),
            ("yesterday", "2026-10-12", "2026-10-12T19:59:00+00:00", "9.90", "10.00"),
        ):
            rows = [{"symbol": "OTHER", "con_id": 1, "bid": "5"},
                    {"symbol": "HELD", "con_id": 300, "bid": bid, "last": last}]
            await db.execute(
                "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
                (snapshot_id, day, observed, json.dumps({"schema": "screener.v1", "rows": rows})),
            )
        await db.commit()
    assert pb.screener_quote(db_path, SESSION, "HELD", 300) == pb.SnapshotQuote(
        price=Decimal("50.30"), observed_at="2026-10-13T19:44:00+00:00"
    )

    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    blind = bracket("blind", "NOPE", status="open", parent=40, filled=50, qty=50)
    broker = connected_broker(bid=None, positions={300: 100, 400: 50})
    for order_id in (31, 41):
        broker.orderStatus(order_id, "Submitted", 0, 100, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"held": held, "blind": blind},
                         clock=Clock(ny(15, 45, 1)), db=db_path)
    capsys.readouterr()
    bridge.flatten()

    # One modification of HELD's own target; NOPE has no price and gets no order.
    [(order_id, sent)] = broker.placed
    assert order_id == 31
    assert (sent.action, sent.orderType, sent.totalQuantity, sent.transmit, sent.parentId) == (
        "SELL", "LMT", 100, True, 30
    )
    assert sent.lmtPrice == pytest.approx(49.54)  # 50.30 x 0.995 x 0.99, floored to the cent
    out = events(capsys)
    assert ("FLATTEN", "HELD: target repriced to 49.54 for 100 sh (no live bid; screener 50.30 @"
            " 2026-10-13T19:44:00+00:00 less 1%)") in out
    assert ("CRITICAL", "NOPE: no quote for flatten — manual action may be needed") in out
    assert held.flatten_sent and not blind.flatten_sent


def test_flatten_alerts_at_1546_1550_1558_and_never_sends_a_second_sell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(ny(15, 44, 30))
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(
        bid=Decimal("50.20"),
        live_orders={31: "Submitted", 32: "PreSubmitted"},
        executions=(execution("e1", 30, 100, 50.0), execution("e2", 31, 40, 50.5)),
        positions={300: 60},
    )
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=clock)
    bridge.stop_event = SteppingStop(clock, 30)
    capsys.readouterr()

    assert bridge.run() == pb.EXIT_OK

    out = events(capsys)
    assert critical(out) == ["HELD: still open after flatten (60 sh) — act in TWS"] * 3
    # H2: one reprice of the target's own order id at 15:45; 15:46, 15:50 and 15:58 only alert.
    [(order_id, first)] = broker.placed
    # The target already sold 40, so its total stays 100 to cover the 60 still held.
    assert (order_id, first.action, first.totalQuantity, first.transmit, first.parentId) == (31, "SELL", 100, True, 30)
    assert first.lmtPrice == pytest.approx(49.94)  # 50.20 x 0.995
    assert held.flatten_price == "49.94"
    assert broker.cancelled == []
    assert clock.value >= ny(15, 58)
    assert ("INFO", "session end: realized P&L 20.00") in out


def test_a_start_that_reaches_1555_still_flattens_once_and_alerts_at_1558(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(ny(15, 55, 10))
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(bid=Decimal("50.00"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("e1", 30, 100, 50.0),), positions={300: 100})
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=clock)
    bridge.stop_event = SteppingStop(clock, 30)
    capsys.readouterr()

    assert bridge.run() == pb.EXIT_OK

    assert [(order_id, sent.lmtPrice, sent.totalQuantity) for order_id, sent in broker.placed] == [(31, 49.0, 100)]
    out = events(capsys)
    assert [text for kind, text in out if kind == "CRITICAL"] == ["HELD: still open after flatten (100 sh) — act in TWS"]


def test_a_stop_after_the_flatten_still_alerts_open_positions_on_the_way_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(ny(15, 55, 10))
    held = bracket("held", "HELD", status="open", parent=30, filled=100, flatten_sent=True,
                   flatten_price="49.75")
    broker = connected_broker(bid=Decimal("50.00"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("e1", 30, 100, 50.0),), positions={300: 100})
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=clock)

    class SupervisorStop(SteppingStop):  # the supervisor's window-end SIGTERM at 15:56
        def wait(self, timeout: float | None = None) -> bool:
            super().wait(timeout)
            self.flag = self.flag or self.clock.value >= ny(15, 56)
            return self.flag

    bridge.stop_event = SupervisorStop(clock, 30)
    capsys.readouterr()

    assert bridge.run() == pb.EXIT_OK

    out = events(capsys)
    assert ("INFO", "stop requested — saving state and disconnecting") in out
    assert [text for kind, text in out if kind == "CRITICAL"] == ["HELD: still open after flatten (100 sh) — act in TWS"]
    assert clock.value < ny(15, 58)
    # A restart after the 15:45 reprice never reprices again (v3 re-sent it as the "late" flatten).
    assert broker.placed == []


# ---- kill switch -------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_paper_kill_switch_blocks_every_new_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.storage.runtime_fence import SQLitePaperKillStore

    db_path, ids = await seed_decisions(tmp_path, monkeypatch)
    assert pb.paper_kill_switch_active(db_path) is False
    await SQLitePaperKillStore().stop(reason="owner stop")
    assert pb.paper_kill_switch_active(db_path) is True

    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, db=db_path)
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []
    assert ("SKIP", "AAPL: kill switch active") in events(capsys)
    assert bridge.brackets[ids["momo"]].skip_reason == "kill switch active"


def test_unreadable_kill_fence_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(sqlite3.OperationalError):
        pb.paper_kill_switch_active(empty)

    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL"),))
    bridge.kill_switch_active = lambda: pb.paper_kill_switch_active(empty)
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []
    assert ("SKIP", "AAPL: kill switch unreadable (OperationalError) — fail closed") in events(capsys)


# ---- process contract: exit codes, signals, event lines -------------------------------------------


@pytest.fixture
def bridge_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config = tmp_path / "config.toml"
    config.write_text('[shadow]\npaper_account_id = "DU1234567"\norder_client_id = 18\n', encoding="utf-8")
    monkeypatch.setattr(pb, "CONFIG", config)
    monkeypatch.setattr(pb, "STATE_DIR", tmp_path / "bridge")
    monkeypatch.setattr(pb, "LEGACY_STATE", tmp_path / "paper_bridge_state.json")
    monkeypatch.setattr(pb, "DB", tmp_path / "ledger.db")
    with closing(sqlite3.connect(tmp_path / "ledger.db")) as connection:  # the fences the connect line reads
        connection.execute("CREATE TABLE autotrader_execution_fences (name TEXT PRIMARY KEY, stopped INTEGER NOT NULL,"
                           " reason TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.commit()
    monkeypatch.setattr(pb, "SYNC_TIMEOUT_S", 0.2)
    monkeypatch.setattr(pb, "ACCOUNTS_TIMEOUT_S", 0.05)
    monkeypatch.setattr(pb, "LOOP_SECONDS", 0)
    monkeypatch.setattr(pb, "QUOTE_TIMEOUT_S", 0.01)
    monkeypatch.setattr(pb, "accepted_decisions", lambda db, day: [])
    monkeypatch.setattr(pb, "paper_kill_switch_active", lambda db: False)
    hard_exits: list[int] = []
    monkeypatch.setattr(pb, "_hard_exit", hard_exits.append)  # a watchdog must never kill pytest
    monkeypatch.setattr(pb, "manual_desk", lambda bridge: None)  # owner orders have their own tests

    def install(broker: FakeBroker | None, clock: Clock) -> None:
        def factory(client_id: int) -> FakeBroker:
            if broker is None:
                pytest.fail("the bridge must not connect to TWS")
            assert client_id == CLIENT_ID
            return broker

        monkeypatch.setattr(pb, "App", factory)
        monkeypatch.setattr(pb, "now_ny", clock)

    return SimpleNamespace(install=install, state_file=tmp_path / "bridge" / "2026-10-13.json",
                           hard_exits=hard_exits)


def test_started_after_1555_exits_0_without_connecting_and_logs_its_sha256(
    bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    store = pb.StateStore(state_dir=pb.STATE_DIR, legacy_path=None, session_date=SESSION)
    store.save({"held": bracket("held", "HELD", status="open", parent=30, filled=100, exit_filled=40)})
    bridge_env.install(None, Clock(ny(15, 56)))

    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_OK

    out = events(capsys)
    assert out[0] == ("INFO", f"bridge sha256={sha256(BRIDGE_PATH.read_bytes()).hexdigest()}")
    assert ("CRITICAL", "HELD: still open per bridge state (60 sh) — act in TWS") in out


def test_normal_session_end_exits_0(bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    broker = FakeBroker()
    bridge_env.install(broker, AutoClock(ny(15, 54, 50), step_seconds=1))

    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_OK

    out = events(capsys)
    assert ("INFO", "session end: realized P&L 0") in out
    assert ("INFO", "bridge stopped (exit 0)") in out
    assert broker.connected is False and bridge_env.state_file.exists()


def test_tws_disconnect_exits_75_for_a_supervised_restart(
    bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    broker = FakeBroker()
    broker.disconnect_after = 2
    bridge_env.install(broker, Clock(ny(10, 0)))

    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_RESTART

    out = events(capsys)
    assert any(kind == "CRITICAL" and text.startswith("TWS connection lost") for kind, text in out)
    assert ("INFO", "bridge stopped (exit 75)") in out
    # Rewritten for v7c (explicit, not weakened): the connect line names the bridge version, now v7c.
    assert any(kind == "INFO" and text.startswith("PAPER bridge v7c connected") for kind, text in out)
    assert bridge_env.state_file.exists()


def test_unexpected_error_after_connect_exits_75(
    bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    class ExplodingBroker(FakeBroker):
        def reqOpenOrders(self) -> None:
            raise RuntimeError("socket exploded")

    broker = ExplodingBroker()
    bridge_env.install(broker, Clock(ny(10, 0)))

    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_RESTART

    assert ("CRITICAL", "bridge error RuntimeError: socket exploded — exit 75 for a supervised restart") in events(capsys)
    assert broker.connected is False


def test_a_ledger_error_is_not_a_restart(
    bridge_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def locked(db: Path, day: date) -> list[Any]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pb, "accepted_decisions", locked)
    broker = FakeBroker()
    bridge_env.install(broker, AutoClock(ny(15, 54, 50), step_seconds=1))

    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_OK

    out = events(capsys)
    assert critical(out) == [
        "decision intake failed (OperationalError: database is locked) — no new decisions until it recovers;"
        " open brackets are still managed"
    ]
    assert ("INFO", "bridge stopped (exit 0)") in out


def test_tws_not_answering_exits_75(bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    bridge_env.install(FakeBroker(answer=False), Clock(ny(9, 25)))
    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_RESTART
    events(capsys)
    assert bridge_env.hard_exits == []


def test_a_tws_that_never_completes_the_handshake_trips_the_watchdog(
    bridge_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    released = threading.Event()
    exits: list[int] = []

    def hard_exit(code: int) -> None:
        exits.append(code)
        released.set()  # os._exit never returns; here the hung handshake is released instead

    class SilentTws(FakeBroker):
        def connect(self, host: str, port: int, client_id: int) -> None:
            released.wait(5)  # TWS accepted TCP but never answers: 9.81 connect() loops forever

    monkeypatch.setattr(pb, "CONNECT_WATCHDOG_S", 0.05)
    monkeypatch.setattr(pb, "_hard_exit", hard_exit)
    bridge_env.install(SilentTws(), Clock(ny(9, 25)))

    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_RESTART

    assert exits == [pb.EXIT_RESTART]
    assert ("CRITICAL", "TWS accepted the connection but sent no order id within 0s — exit 75") in events(capsys)


def test_no_managed_accounts_exits_75_for_a_retry(
    bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    broker = FakeBroker(accounts="")
    bridge_env.install(broker, Clock(ny(9, 25)))

    # A slow TWS is transient, unlike a non-paper account: 75, never the final 78.
    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_RESTART

    assert broker.connected is False and broker.placed == []
    out = events(capsys)
    assert any(kind == "CRITICAL" and text.startswith("TWS reported no managed accounts") for kind, text in out)


def test_sigterm_saves_state_disconnects_and_exits_0(
    bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    broker = FakeBroker()
    seen: dict[str, Any] = {}

    def deliver_sigterm(count: int) -> None:
        if count == 2:
            seen["handler"] = signal.getsignal(signal.SIGTERM)
            seen["handler"](signal.SIGTERM, None)  # what the OS would invoke

    broker.on_connected_check = deliver_sigterm
    original = signal.getsignal(signal.SIGTERM)
    bridge_env.install(broker, Clock(ny(11, 0)))

    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_OK

    assert seen["handler"] is pb._request_stop
    assert signal.getsignal(signal.SIGTERM) is original
    assert broker.connected is False and bridge_env.state_file.exists()
    out = events(capsys)
    assert ("INFO", "stop requested — saving state and disconnecting") in out
    assert ("INFO", "bridge stopped (exit 0)") in out


def test_refusals_exit_78(bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    bridge_env.install(None, Clock(ny(10, 0)))
    assert pb.main(["--date", "2026-10-14"]) == pb.EXIT_REFUSED

    broker = FakeBroker(accounts="U1234567,DU1234567")
    broker.disconnect_after = 3  # without the paper check the run ends fast (75) instead of hanging
    bridge_env.install(broker, Clock(ny(10, 0)))
    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_REFUSED
    assert broker.placed == [] and broker.connected is False
    out = events(capsys)
    assert ("CRITICAL", "refusing: TWS reports a non-paper account ['U1234567', 'DU1234567']") in out


def test_log_lines_always_parse(capsys: pytest.CaptureFixture[str]) -> None:
    pb.log("FILL", "AAPL: 100/100 sh @ 50.0000 (MOMO_HOD_V2)")
    pb.log("BOGUS", "multi\nline  message")
    assert events(capsys) == [
        ("FILL", "AAPL: 100/100 sh @ 50.0000 (MOMO_HOD_V2)"),
        ("INFO", "BOGUS multi line message"),
    ]


# ---- execution-safety review 2026-10-12: one regression per finding ------------------------------------


def snap(parent: int, target: int, stop: int, recorded: int, position: int | None) -> Any:
    return pb.FillSnapshot(parent=parent, target=target, stop=stop, recorded_exits=recorded,
                           position=None if position is None else Decimal(position))


@pytest.mark.parametrize(("snapshot", "leg", "expected"), [
    # A leg's own fills never feed its total: IBKR keeps 60 working for the 60 still held.
    (snap(100, 40, 0, 40, 60), "target", (100, 60, None, "")),
    (snap(100, 40, 0, 40, 60), "stop", (60, 60, None, "")),
    # H1: 30 more target fills landed after the refresh; v3 sent 70 + the stale 60 = 130.
    (snap(100, 70, 0, 40, 30), "target", (100, 30, None, "")),
    # Exits the snapshot cannot attribute (executions missing after a restart) shrink both legs.
    (snap(100, 0, 0, 40, 60), "target", (60, 60, None, "")),
    (snap(100, 0, 0, 40, 60), "stop", (60, 60, None, "")),
    # H2: the owner sold 30 by hand in TWS; a flat or short account gets nothing.
    (snap(100, 40, 0, 40, 30), "target", (70, 30, 60, "")),
    (snap(100, 40, 0, 40, 0), "target", (40, 0, 60, "")),
    (snap(100, 40, 0, 40, -5), "stop", (0, 0, 60, "")),
    (snap(60, 40, 30, 40, 20), "target", (0, 0, None, "exit fills 70 exceed entry fills 60")),
    (snap(100, 40, 0, 40, None), "target", (0, 0, None, "account position unknown")),
])
def test_exit_totals_follow_the_one_snapshot_rule(snapshot: Any, leg: str, expected: tuple[Any, ...]) -> None:
    plan = pb.plan_exit(snapshot, leg)
    assert (plan.total, plan.working, plan.capped_from, plan.problem) == expected


def test_no_snapshot_yields_an_exit_above_entry_fills_or_the_account_position() -> None:
    values = range(5)
    for parent, target, stop, recorded in itertools.product(values, repeat=4):
        for position in (None, -1, *values):
            for leg in ("target", "stop"):
                plan = pb.plan_exit(snap(parent, target, stop, recorded, position), leg)
                own, other = (target, stop) if leg == "target" else (stop, target)
                if max(recorded, target + stop) > parent or position is None:
                    assert plan.problem and plan.working == 0
                    continue
                assert not plan.problem
                assert own <= plan.total <= parent - other
                assert plan.working == plan.total - own <= max(0, position)


@pytest.mark.parametrize("position_update", [True, False], ids=["position-updated", "position-stale"])
def test_h1_flatten_quantity_comes_from_one_snapshot_taken_after_the_quote_wait(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], position_update: bool
) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(live_orders={31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("e1", 30, 100, 50.0), execution("t1", 31, 40, 50.5)),
                              positions={300: 60})
    broker.reqOpenOrders()
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=Clock(ny(15, 45, 1)))
    bridge.refresh(held)
    assert (held.exit_filled, held.remaining) == (40, 60)

    def quote_while_the_target_fills(request_id: int, contract: Any, *args: Any) -> None:
        broker.execDetails(-1, None, execution("t2", 31, 30, 49.9))  # lands during the quote wait
        if position_update:
            broker.position(ACCOUNT, SimpleNamespace(conId=300), 30.0, 50.0)
        broker.tickPrice(request_id, 1, 49.80, None)

    broker.reqMktData = quote_while_the_target_fills  # type: ignore[method-assign]
    capsys.readouterr()
    bridge.flatten()

    # v3 sent 130 (70 target fills + the stale 60): IBKR would have sold 60 of the 30 held.
    [(order_id, sent)] = broker.placed
    assert (order_id, sent.totalQuantity) == (31, 100)
    assert sent.lmtPrice == pytest.approx(49.55)  # 49.80 x 0.995
    broker.execDetails(-1, None, execution("t3", 31, 30, 49.6))  # IBKR sells exactly the 30 held
    bridge.refresh(held)
    assert (held.status, held.exit_filled, held.net_held) == ("closed", 100, 0)
    bridge.verify_flat()
    out = events(capsys)
    assert ("FLATTEN", "HELD: target repriced to 49.55 for 30 sh (bid 49.8)") in out
    assert ("FLAT", "no open engine positions") in out
    assert critical(out) == []


def test_h1_exit_fills_above_entry_fills_block_modifies_and_are_never_reported_flat(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=60)
    broker = connected_broker(live_orders={31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("e1", 30, 60, 50.0), execution("t1", 31, 40, 50.5)),
                              positions={300: 20})
    broker.reqOpenOrders()
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=Clock(ny(15, 45, 1)))
    bridge.refresh(held)

    def quote_while_the_stop_fills(request_id: int, contract: Any, *args: Any) -> None:
        broker.execDetails(-1, None, execution("s1", 32, 30, 49.0))
        broker.tickPrice(request_id, 1, 50.00, None)

    broker.reqMktData = quote_while_the_stop_fills  # type: ignore[method-assign]
    capsys.readouterr()
    bridge.flatten()
    assert broker.placed == []

    bridge.refresh(held)
    assert (held.status, held.net_held) == ("needs_attention", -10)
    bridge.verify_flat()
    out = events(capsys)
    assert critical(out) == [
        "HELD: flatten of target 31 not sent — exit fills 70 exceed entry fills 60; act in TWS",
        "HELD: exits 70 sh exceed entry fills 60 sh — the account may be short 10 sh; act in TWS",
        "HELD: exits exceed entry fills by 10 sh — the account may be short; act in TWS",
    ]
    assert not [kind for kind, _ in out if kind in {"CLOSED", "FLAT"}]


@pytest.mark.parametrize("owner_sold", [70, 100])
def test_h2_exits_never_exceed_the_account_position_and_1550_only_alerts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], owner_sold: int
) -> None:
    clock = Clock(ny(15, 44, 30))
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    left = 100 - owner_sold
    # Before 15:45 the owner sold by hand in TWS (client 0): not the bridge's fill, but the account's shares.
    broker = connected_broker(
        bid=Decimal("50.20"), live_orders={31: "Submitted", 32: "PreSubmitted"},
        executions=(execution("e1", 30, 100, 50.0), execution("m1", 7, owner_sold, 50.1, client_id=0)),
        positions={300: left},
    )
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=clock)
    bridge.stop_event = SteppingStop(clock, 30)
    capsys.readouterr()

    assert bridge.run() == pb.EXIT_OK

    if left:
        # One reprice at 15:45, limited to the account's shares; v3 sold 100 at 15:45 and repriced again at 15:50.
        [(order_id, sent)] = broker.placed
        assert (order_id, sent.totalQuantity) == (31, left)
        outcome = f"limited to {left} sh"
    else:
        assert broker.placed == []
        outcome = "not sent"
    assert critical(events(capsys)) == [
        f"HELD: account holds {left} sh but the bracket expects 100 — flatten of target 31 {outcome}; act in TWS",
        f"HELD: account holds {left} sh but the bracket expects 100 — its exit orders could sell shares the"
        " account no longer holds; check TWS",
        "HELD: still open after flatten (100 sh) — act in TWS",  # 15:46
        "HELD: still open after flatten (100 sh) — act in TWS",  # 15:50: alert only
        "HELD: still open after flatten (100 sh) — act in TWS",  # 15:58
    ]


def test_h2_a_bracket_is_repriced_at_most_once_whoever_asks(tmp_path: Path) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(bid=Decimal("50.20"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              positions={300: 100})
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=Clock(ny(15, 45, 1)))

    assert bridge.flatten_bracket(held) is True
    assert bridge.flatten_bracket(held) is False  # e.g. a later manual "close" through the bridge
    bridge.flatten(pb.LATE_FLATTEN_DISCOUNT)
    assert [order_id for order_id, _ in broker.placed] == [31]


def test_h2_an_account_holding_less_than_its_bracket_is_critical_after_a_grace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(ny(11, 0))
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(positions={300: 0})  # the entry filled; its position update trails
    for order_id, status in ((30, "Filled"), (31, "Submitted"), (32, "PreSubmitted")):
        broker.orderStatus(order_id, status, 100 if order_id == 30 else 0, 0, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=clock)
    capsys.readouterr()
    bridge.tick()
    clock.advance(10)
    bridge.tick()
    broker.position(ACCOUNT, SimpleNamespace(conId=300), 100.0, 50.0)
    bridge.tick()
    assert critical(events(capsys)) == []  # a position update that trails its fill is not an alarm

    broker.position(ACCOUNT, SimpleNamespace(conId=300), 0.0, 50.0)  # the owner sells all 100 in TWS
    for _ in range(3):
        bridge.tick()
        clock.advance(10)
    assert critical(events(capsys)) == [
        "HELD: account holds 0 sh but the bracket expects 100 — its exit orders could sell shares the account"
        " no longer holds; check TWS"
    ]


def test_m1_a_ledger_error_never_blocks_the_flatten(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(bid=Decimal("50.00"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              positions={300: 100})
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=Clock(ny(15, 45, 5)))
    reads: list[int] = []

    def locked_ledger() -> list[Any]:
        reads.append(1)
        if len(reads) <= 2:
            raise sqlite3.OperationalError("database is locked")
        return []

    bridge.decisions = locked_ledger
    capsys.readouterr()
    for _ in range(3):
        bridge.tick()

    # v3 raised out of the tick before the flatten, exited 75 and looped without ever flattening.
    assert [order_id for order_id, _ in broker.placed] == [31]
    out = events(capsys)
    assert ("FLATTEN", "HELD: target repriced to 49.75 for 100 sh (bid 50.0)") in out
    assert critical(out) == [
        "decision intake failed (OperationalError: database is locked) — no new decisions until it recovers;"
        " open brackets are still managed"
    ]
    assert ("INFO", "decision intake recovered after 2 failed attempts") in out


def test_m2_resize_uses_the_dead_parents_fresh_fill_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    held = bracket("held", "HELD", status="open", parent=10, filled=40, parent_cancel_requested=True)
    broker = connected_broker(executions=(execution("p", 10, 40, 50.0),), positions={100: 50})
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    for order_id, status in ((10, "Submitted"), (11, "Submitted"), (12, "PreSubmitted")):
        broker.orderStatus(order_id, status, 0, 0, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"held": held})
    bridge.refresh(held)
    # The parent's last 10 shares and its Cancelled status land after the refresh: only the
    # status report (filled=50) knows them yet.
    broker.orderStatus(10, "Cancelled", 50, 0, 50.0)
    capsys.readouterr()
    bridge.resize_children(held)

    # v3 sized both legs to the stale 40 and never looked again.
    assert [(oid, sent.orderType, sent.totalQuantity) for oid, sent in broker.placed] == [
        (11, "LMT", 50), (12, "STP", 50),
    ]
    assert held.resized_qty == 50
    broker.placed.clear()
    broker.execDetails(-1, None, execution("p2", 10, 10, 50.0))  # the execution report catches up
    bridge.refresh(held)
    bridge.resize_children(held)
    assert broker.placed == [] and held.filled == 50
    assert [text for kind, text in events(capsys) if kind == "RESIZE"] == ["HELD: exits set to the 50 filled shares"]


def test_m2_a_resize_is_redone_when_the_fill_count_or_the_position_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held = bracket("held", "HELD", status="open", parent=10, filled=40, parent_cancel_requested=True)
    broker = connected_broker(executions=(execution("p", 10, 40, 50.0),), positions={100: 40})
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    for order_id, status in ((10, "Cancelled"), (11, "Submitted"), (12, "PreSubmitted")):
        broker.orderStatus(order_id, status, 0, 0, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"held": held})
    capsys.readouterr()
    bridge.tick()
    assert [(oid, sent.totalQuantity) for oid, sent in broker.placed] == [(11, 40), (12, 40)]
    assert held.resized_qty == 40

    # A late execution report: the dead parent had filled 50, but the account still shows 40.
    broker.placed.clear()
    broker.execDetails(-1, None, execution("p2", 10, 10, 50.0))
    bridge.tick()
    assert broker.placed == [] and held.resized_qty == 40  # capped totals equal the last ones sent

    broker.position(ACCOUNT, SimpleNamespace(conId=100), 50.0, 50.0)  # the position catches up
    bridge.tick()
    assert [(oid, sent.totalQuantity) for oid, sent in broker.placed] == [(11, 50), (12, 50)]
    assert held.resized_qty == 50
    broker.placed.clear()
    bridge.tick()
    assert broker.placed == []
    out = events(capsys)
    assert [text for kind, text in out if kind == "RESIZE"] == [
        "HELD: exits set to the 40 filled shares", "HELD: exits set to the 50 filled shares",
    ]
    # N4: the position caught up within the grace, so the cap was a notice, never an alarm.
    assert critical(out) == []
    assert ("INFO", "HELD: account holds 40 sh but the bracket expects 50 — resize of stop 12 limited to 40 sh"
            " — re-checked while the position updates") in out


def test_m2_dead_exit_legs_are_critical_and_never_reported_as_resized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held = bracket("held", "HELD", status="open", parent=10, filled=40, parent_cancel_requested=True)
    broker = connected_broker(executions=(execution("p", 10, 40, 50.0),), positions={100: 40})
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    for order_id in (10, 11, 12):
        broker.orderStatus(order_id, "Cancelled", 0, 0, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"held": held})
    capsys.readouterr()
    bridge.tick()
    bridge.tick()

    assert broker.placed == []
    out = events(capsys)
    assert not [text for kind, text in out if kind == "RESIZE"]  # v3 logged RESIZE for two dead legs
    assert critical(out) == [
        "HELD: target order 11 is not live (Cancelled) — 40 sh without a target; act in TWS",
        "HELD: stop order 12 is not live (Cancelled) — 40 sh unprotected; act in TWS",
    ]


def test_m2_an_open_position_without_a_live_stop_is_critical_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    filling = bracket("fill", "FILL", status="open", parent=40, filled=100)
    broker = connected_broker(positions={300: 100, 400: 100})
    for order_id, status, filled in ((31, "Submitted", 0), (32, "Cancelled", 0), (41, "Filled", 100),
                                     (42, "Cancelled", 0)):
        broker.orderStatus(order_id, status, filled, 0, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"held": held, "fill": filling})
    capsys.readouterr()
    bridge.tick()
    bridge.tick()

    # FILL's target filled (its executions are on the way; OCA cancelled the stop): no alarm.
    assert critical(events(capsys)) == [
        "HELD: stop order 32 is not live (Cancelled) — 100 sh unprotected; act in TWS"
    ]


@pytest.mark.parametrize(("intent", "reason"), [
    ({"quantity": 374100, "entry_limit": "26.75", "stop_price": "26.66", "target_price": "27.00"},
     "order notional 10007175.00 exceeds 25% of net liquidation 665000.00"),
    ({"quantity": 3000, "entry_limit": "50.00", "stop_price": "47.00", "target_price": "56.00"},
     "order risk 9000.00 exceeds 1% of net liquidation 665000.00"),
])
def test_m3_oversized_orders_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], intent: dict[str, Any], reason: str
) -> None:
    row = replace(decision_row("big", "BIG"), payload_json=json.dumps({"risk": {"intent": intent}}))
    broker = connected_broker(bid=Decimal("26.80"))
    bridge = make_bridge(tmp_path, broker, decisions=(row,))
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []  # v3 sent the 374,100-share decision
    assert ("SKIP", f"BIG (MOMO_HOD_V2): {reason}") in events(capsys)


@pytest.mark.parametrize(("ghost_filled", "sent"), [(100, False), (0, True)])
def test_m4_unreconciled_brackets_that_may_hold_shares_count_toward_max_open(
    tmp_path: Path, ghost_filled: int, sent: bool
) -> None:
    ghost = bracket("g", "GHST", status="needs_attention", parent=20, filled=ghost_filled)
    first = bracket("a", "AAA", status="open", parent=40, filled=100)
    second = bracket("b", "BBB", status="open", parent=50, filled=100)
    broker = connected_broker(bid=Decimal("50.10"), positions={400: 100, 500: 100})
    for order_id in (41, 42, 51, 52):
        broker.orderStatus(order_id, "PreSubmitted", 0, 100, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"g": ghost, "a": first, "b": second},
                         decisions=(decision_row("n", "NEWX"),))
    bridge.tick()

    if sent:  # an unreconciled bracket that never filled holds nothing
        assert [order_id for order_id, _ in broker.placed] == [100, 101, 102]
    else:  # v3 sent a fourth position here
        assert broker.placed == []
        assert bridge.brackets["n"].skip_reason == "MOMO_HOD_V2: max 3 positions already active"


def test_l1_rejections_of_restored_legs_are_critical(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker()
    make_bridge(tmp_path, broker, brackets={"held": held})  # a restart: the legs come from state
    capsys.readouterr()
    for order_id, code, message in (
        (31, 201, "Order rejected - reason: test"),
        (31, 10006, "Missing parent order"),
        (30, 10147, "OrderId 30 that needs to be cancelled is not found."),
        (30, 10148, "OrderId 30 that needs to be cancelled cannot be cancelled, state: Filled."),
        (32, 161, "Cancel attempted when order is not in a cancellable state."),
        (77, 201, "Order rejected - reason: not ours"),
    ):
        broker.error(order_id, code, message)

    assert events(capsys) == [
        ("CRITICAL", "TWS 31 201 Order rejected - reason: test"),
        ("CRITICAL", "TWS 31 10006 Missing parent order"),
        ("CRITICAL", "TWS 30 10147 OrderId 30 that needs to be cancelled is not found."),
        ("CRITICAL", "TWS 30 10148 OrderId 30 that needs to be cancelled cannot be cancelled, state: Filled."),
        ("CRITICAL", "TWS 32 161 Cancel attempted when order is not in a cancellable state."),
        ("INFO", "TWS 77 201 Order rejected - reason: not ours"),
    ]


def test_l3_a_parent_cancel_lost_while_disconnected_is_resent_after_a_restart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # v3 kept the flag although 9.81 drops a cancelOrder sent while disconnected without an error.
    working = bracket("work", "WORK", status="sent", parent=40, parent_cancel_requested=True)
    broker = connected_broker(live_orders={40: "Submitted", 41: "PreSubmitted", 42: "PreSubmitted"})
    bridge = make_bridge(tmp_path, broker, brackets={"work": working},
                         clock=Clock(DECIDED + timedelta(minutes=11)))
    capsys.readouterr()
    bridge.startup()
    assert working.parent_cancel_requested is False
    bridge.tick()
    bridge.tick()

    assert broker.cancelled == [40]
    assert working.parent_cancel_requested is True
    out = events(capsys)
    assert ("INFO", "entry WORK: still working at IBKR after an earlier cancel request — the cancel will be"
            " re-sent") in out
    assert ("CANCEL", "entry WORK: not filled in 10 min (filled 0/100)") in out


def test_l4_unmanaged_live_orders_block_their_symbol_and_take_a_slot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The slot mechanism under the v5 global cap of 3 (v7b's shipped sum is 6).
    monkeypatch.setattr(pb, "IDENTITY_CAPS", {"MOMO_HOD_V2": pb.IDENTITY_CAPS["MOMO_HOD_V2"]})
    held = bracket("a", "AAA", status="open", parent=40, filled=100)
    broker = connected_broker(
        bid=Decimal("50.10"),
        live_orders={41: "Submitted", 42: "PreSubmitted", 77: "Submitted", 88: "PreSubmitted"},
        executions=(execution("e1", 40, 100, 50.0),), positions={400: 100},
    )
    bridge = make_bridge(tmp_path, broker, brackets={"a": held})
    bridge.startup()
    bridge.decisions = lambda: [decision_row("n1", "NEWX"), decision_row("s77", "SYM77")]
    bridge.tick()

    assert broker.placed == []  # AAA + SYM77 + SYM88 fill the three slots
    assert bridge.brackets["n1"].skip_reason == "max 3 positions/entries already active"
    assert bridge.brackets["s77"].skip_reason == "symbol has a live order at IBKR the bridge does not manage"
    assert {77, 88} <= broker.own_order_ids

    broker.orderStatus(88, "Cancelled", 0, 0, 0)  # the owner cancels the stray order in TWS
    bridge.decisions = lambda: [decision_row("n2", "NEWY")]
    bridge.tick()
    assert [order_id for order_id, _ in broker.placed] == [100, 101, 102]


def test_l5_fills_are_scoped_to_the_session_account_and_bridge_orders(tmp_path: Path) -> None:
    held = bracket("held", "HELD", status="sent", parent=30)
    broker = connected_broker()
    bridge = make_bridge(tmp_path, broker, brackets={"held": held})
    today, ref = "20261013  10:00:05", "autotrader:held"
    for item in (
        # 9.81 reqExecutions replays 24 h: yesterday's order 30 (a reset id sequence) is not today's.
        execution("y.01", 30, 50, 40.0, time="20261012  15:59:00", acctNumber=ACCOUNT, orderRef=ref),
        execution("o.01", 30, 50, 40.0, time=today, acctNumber="DU999999", orderRef=ref),
        execution("m.01", 30, 50, 40.0, time=today, acctNumber=ACCOUNT, orderRef="manual:7"),
        execution("t.01", 30, 60, 50.0, time=today, acctNumber=ACCOUNT, orderRef=ref),
    ):
        broker.execDetails(-1, None, item)
    bridge.refresh(held)

    assert (held.filled, held.avg_entry, held.status) == (60, "50.0000", "open")


def test_l5_a_corrected_execution_replaces_the_original_and_overfills_are_capped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held = bracket("held", "HELD", status="sent", parent=30)
    broker = connected_broker()
    bridge = make_bridge(tmp_path, broker, brackets={"held": held})
    capsys.readouterr()
    broker.execDetails(-1, None, execution("0000e0d5.6576fd38.01.01", 30, 60, 50.0))
    broker.execDetails(-1, None, execution("0000e0d5.6576fd38.01.02", 30, 40, 50.0))  # IBKR's correction
    broker.execDetails(-1, None, execution("0000e0d5.6576fd38.01.01", 30, 60, 50.0))  # the original, replayed
    assert broker.fills_for(30)[0] == 40
    bridge.refresh(held)
    assert held.filled == 40

    broker.execDetails(-1, None, execution("0000e0d5.6576fd39.01.01", 30, 100, 50.0))
    bridge.refresh(held)
    bridge.refresh(held)
    assert held.filled == 100  # never more than the order's 100
    out = events(capsys)
    assert ("INFO", "execution 0000e0d5.6576fd38.01.02 corrects order 30: 60 -> 40 sh") in out
    assert critical(out) == [
        "HELD: executions for order 30 add up to 140 sh, more than its 100 — counted as 100; check TWS"
    ]


def test_l6_decisions_expire_with_their_risk_approval(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def expiring(decision_id: str, symbol: str, seconds: int) -> Any:
        return decision_row(decision_id, symbol,
                            expires_at=(DECIDED + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat())

    rows = (
        expiring("stale", "OLDX", 15),  # the engine's approval ran out at +15 s
        decision_row("plain", "PLNX"),  # no expiry in the intent: the engine's 15 s
        expiring("fresh", "NEWX", 30),
    )
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=rows, clock=Clock(DECIDED + timedelta(seconds=20)))
    capsys.readouterr()
    bridge.tick()

    # v3 accepted all three for 60 s.
    assert [order_id for order_id, _ in broker.placed] == [100, 101, 102]
    assert bridge.brackets["fresh"].status == "sent"
    out = events(capsys)
    assert ("SKIP", "OLDX (MOMO_HOD_V2): decision expired (20s old)") in out
    assert ("SKIP", "PLNX (MOMO_HOD_V2): decision expired (20s old)") in out


def test_l6_an_approval_that_expires_during_the_quote_wait_is_not_sent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=14))
    broker = connected_broker()

    def slow_quote(request_id: int, contract: Any, *args: Any) -> None:
        clock.advance(2)
        broker.tickPrice(request_id, 1, 50.10, None)

    broker.reqMktData = slow_quote  # type: ignore[method-assign]
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL"),), clock=clock)
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []
    assert ("SKIP", "AAPL (MOMO_HOD_V2): decision expired (16s old)") in events(capsys)


def test_l6_an_identical_setup_is_sent_only_once(tmp_path: Path) -> None:
    setup = "f" * 64
    first = bracket("first", "AAPL", status="closed", parent=30, filled=100, exit_filled=100,
                    setup_fingerprint=setup)
    rows = (decision_row("again", "AAPL", setup_fingerprint=setup),
            decision_row("other", "MSFT", setup_fingerprint="e" * 64))
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, brackets={"first": first}, decisions=rows)
    bridge.tick()

    assert bridge.brackets["again"].skip_reason == "setup already sent (decision first)"
    assert [order_id for order_id, _ in broker.placed] == [100, 101, 102]
    assert bridge.brackets["other"].setup_fingerprint == "e" * 64


def test_l8_tws_codes_are_logged_by_severity(capsys: pytest.CaptureFixture[str]) -> None:
    broker = FakeBroker()
    for code, message in (
        (2110, "Connectivity between Trader Workstation and server is broken."),
        (2103, "Market data farm connection is broken:usfarm"),
        (2105, "HMDS data farm connection is broken:ushmds"),
        (2157, "Sec-def data farm connection is broken:secdefil"),
        (2104, "Market data farm connection is OK:usfarm"),
        (2106, "HMDS data farm connection is OK:ushmds"),
        (1100, "Connectivity between IB and Trader Workstation has been lost."),
    ):
        broker.error(-1, code, message)

    assert events(capsys) == [
        ("CRITICAL", "TWS -1 2110 Connectivity between Trader Workstation and server is broken."),
        ("INFO", "TWS -1 2103 Market data farm connection is broken:usfarm"),
        ("INFO", "TWS -1 2105 HMDS data farm connection is broken:ushmds"),
        ("INFO", "TWS -1 2157 Sec-def data farm connection is broken:secdefil"),
        ("CRITICAL", "TWS -1 1100 Connectivity between IB and Trader Workstation has been lost."),
    ]


def test_modifications_start_from_the_order_tws_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reported = pb.order("SELL", "LMT", 100, ACCOUNT, "autotrader:held", lmtPrice=51.0, tif="GTC",
                        parentId=30, transmit=True)
    reported.ocaGroup, reported.ocaType = "1528474519", 3  # assigned by TWS to the bracket's children
    reported.eTradeOnly = True  # the 9.81 default that current TWS rejects
    reported.settlingFirm = "?"  # TWS placeholder for a field it does not report
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(bid=Decimal("50.20"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              order_objects={31: reported}, positions={300: 100})
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=Clock(ny(15, 45, 1)))
    capsys.readouterr()
    bridge.flatten()

    [(order_id, sent)] = broker.placed
    assert order_id == 31 and sent is not reported and reported.lmtPrice == 51.0  # the cache is never mutated
    assert (sent.ocaGroup, sent.ocaType, sent.parentId, sent.orderRef, sent.tif, sent.account) == (
        "1528474519", 3, 30, "autotrader:MOMO_HOD_V2:held", "GTC", ACCOUNT
    )
    assert (sent.totalQuantity, sent.transmit, sent.eTradeOnly, sent.firmQuoteOnly, sent.settlingFirm) == (
        100, True, False, False, ""
    )
    assert sent.lmtPrice == pytest.approx(49.94)
    assert not any("without its OCA fields" in text for _, text in events(capsys))


def test_a_modification_never_rewrites_an_order_of_another_shape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reported = pb.order("BUY", "MKT", 100, ACCOUNT, "autotrader:held")  # e.g. changed by hand in TWS
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(bid=Decimal("50.20"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              order_objects={31: reported}, positions={300: 100})
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=Clock(ny(15, 45, 1)))
    capsys.readouterr()
    bridge.flatten()

    assert broker.placed == [] and not held.flatten_sent
    assert critical(events(capsys)) == [
        "HELD: target 31 at IBKR is BUY MKT (DU1234567), not the bridge's SELL LMT — not modified; act in TWS"
    ]


# ---- execution-safety re-review of v4 (2026-10-12): N1-N7 ----------------------------------------------


@pytest.mark.parametrize(("held", "reason"), [
    (500, "account already holds 500 — bot only trades symbols it alone holds"),  # the owner's own shares
    (-200, "account already holds -200 — bot only trades symbols it alone holds"),  # the owner is short
    (None, "account position unknown"),
])
def test_n1_entries_only_in_symbols_the_account_does_not_hold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], held: int | None, reason: str
) -> None:
    # v4 entered here; its exit cap then read the owner's shares as the bot's (or saw a short and froze).
    broker = connected_broker(bid=Decimal("50.10"), positions={} if held is None else {4931: held})
    if held is None:
        broker.positions_done.clear()  # TWS has not reported positions yet
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL"),))
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []
    assert ("SKIP", f"AAPL (MOMO_HOD_V2): {reason}") in events(capsys)


def test_n2_every_error_on_an_own_order_is_critical_unless_informational(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker()
    make_bridge(tmp_path, broker, brackets={"held": held})
    capsys.readouterr()
    rejections = (201, 320, 321, 10268, 10269, 10270, 434, 383, 163, 136, 10318, 110, 2161)
    notices = (202, 399, 2109, 2148, 10311)
    for code in rejections + notices:
        broker.error(31, code, f"text {code}")
    broker.error(77, 321, "not one of ours")

    assert events(capsys) == (
        [("CRITICAL", f"TWS 31 {code} text {code}") for code in rejections]
        + [("INFO", f"TWS 31 {code} text {code}") for code in notices]
        + [("INFO", "TWS 77 321 not one of ours")]
    )


def _dead_partial_entry(tmp_path: Path, *, echo: bool, clock: Clock) -> tuple[Any, FakeBroker, Any]:
    held = bracket("held", "HELD", status="open", parent=10, filled=40, parent_cancel_requested=True)
    broker = connected_broker(executions=(execution("p", 10, 40, 50.0),), positions={100: 40})
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    for order_id, status in ((10, "Cancelled"), (11, "Submitted"), (12, "PreSubmitted")):
        broker.orderStatus(order_id, status, 40 if order_id == 10 else 0, 0, 0)
    broker.echo_orders = echo
    return held, broker, make_bridge(tmp_path, broker, brackets={"held": held}, clock=clock)


def test_n2_a_resize_counts_as_done_only_once_tws_echoes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(ny(11, 0))
    held, broker, bridge = _dead_partial_entry(tmp_path, echo=False, clock=clock)
    bridge.tick()
    assert [(oid, sent.totalQuantity) for oid, sent in broker.placed] == [(11, 40), (12, 40)]
    assert held.resized_qty == 0  # sent, not yet confirmed

    for order_id, sent in list(broker.placed):  # TWS's openOrder echo arrives after the tick
        echo = copy.copy(sent)
        echo.clientId = CLIENT_ID
        broker.openOrder(order_id, SimpleNamespace(symbol="HELD", conId=100), echo, SimpleNamespace(status="Submitted"))
    clock.advance(2)
    bridge.tick()
    assert len(broker.placed) == 2 and held.resized_qty == 40
    assert critical(events(capsys)) == []


@pytest.mark.parametrize("echo", [False, True], ids=["no-echo", "echo-then-error"])
def test_n2_a_rejected_resize_is_resent_three_times_then_critical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], echo: bool
) -> None:
    clock = Clock(ny(11, 0))
    held, broker, bridge = _dead_partial_entry(tmp_path, echo=echo, clock=clock)
    original = broker.placeOrder

    def rejecting(order_id: int, contract: Any, order: Any) -> None:  # TWS keeps the old order
        original(order_id, contract, order)
        broker.error(order_id, 321, "Error validating request")

    broker.placeOrder = rejecting  # type: ignore[method-assign]
    capsys.readouterr()
    for _ in range(10):
        bridge.tick()
        clock.advance(2)

    # v4 logged the 321 as INFO, marked the resize done and never looked again.
    assert [order_id for order_id, _ in broker.placed] == [11, 12] * 4
    assert held.resized_qty == 0
    out = critical(events(capsys))
    assert out.count("TWS 11 321 Error validating request") == 4
    assert out[-2:] == [
        "HELD: TWS did not confirm the resize of target 11 (40 sh @ 51.00) after 4 attempts — act in TWS",
        "HELD: TWS did not confirm the resize of stop 12 (40 sh @ 49.50) after 4 attempts — act in TWS",
    ]


def test_n2_an_unconfirmed_flatten_is_resent_at_the_same_price_then_critical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(bid=Decimal("50.20"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("e1", 30, 100, 50.0),), positions={300: 100})
    broker.reqOpenOrders()
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    broker.echo_orders = False
    clock = Clock(ny(15, 45, 1))
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=clock)
    capsys.readouterr()
    for _ in range(7):
        bridge.tick()
        clock.advance(2)

    assert [(order_id, sent.totalQuantity, sent.lmtPrice) for order_id, sent in broker.placed] == [
        (31, 100, pytest.approx(49.94))
    ] * 4  # one quote, one price: the same modification, never a new sale
    assert "HELD: TWS did not confirm the flatten of target 31 (100 sh @ 49.94) after 4 attempts — act in TWS" in (
        critical(events(capsys))
    )


def test_n2_a_live_exit_leg_on_a_finished_bracket_is_critical_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(ny(11, 0))
    done = bracket("done", "DONE", status="closed", parent=50, filled=100, exit_filled=100)
    broker = connected_broker(bid=Decimal("50.10"))
    for order_id, status, filled in ((51, "Filled", 100), (52, "PreSubmitted", 0)):  # OCA never cancelled the stop
        broker.orderStatus(order_id, status, filled, 0, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"done": done}, clock=clock)
    capsys.readouterr()
    bridge.tick()
    clock.advance(10)
    bridge.tick()
    assert critical(events(capsys)) == []  # an OCA cancel may trail the fill by a moment

    clock.advance(10)
    bridge.tick()
    bridge.decisions = lambda: [decision_row("again", "DONE", created=clock.value)]
    bridge.tick()
    assert critical(events(capsys)) == ["DONE: order 52 is still live at IBKR for a closed bracket — check TWS"]
    assert bridge.brackets["again"].skip_reason == "symbol has a live order at IBKR the bridge does not manage"


def encode_place_order(order_id: int, contract: Any, order: Any) -> list[str]:
    """The fields the real ibapi 9.81 placeOrder writes to the TWS socket."""
    from ibapi.client import EClient
    from ibapi.server_versions import MAX_CLIENT_VER
    from ibapi.wrapper import EWrapper

    class Socket:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def isConnected(self) -> bool:
            return True

        def sendMsg(self, message: bytes) -> int:
            self.sent.append(message)
            return len(message)

    client = EClient(EWrapper())
    client.conn = Socket()
    client.serverVersion_ = MAX_CLIENT_VER  # what 9.81 negotiates with any current TWS
    client.connState = EClient.CONNECTED
    client.placeOrder(order_id, contract, order)
    assert client.conn.sent, "the 9.81 encoder refused the order"
    return client.conn.sent[-1][4:].decode().split("\0")[:-1]


@pytest.mark.parametrize("leg", ["target", "stop"])
def test_n3_modifies_encode_exactly_like_the_placement_tws_accepted(tmp_path: Path, leg: str) -> None:
    kind, price_field, order_id, placed_price = (
        ("LMT", "lmtPrice", 31, 51.0) if leg == "target" else ("STP", "auxPrice", 32, 49.5)
    )
    placement = pb.order("SELL", kind, 100, ACCOUNT, "autotrader:MOMO_HOD_V2:held", tif="GTC", parentId=30,
                         transmit=leg == "stop", **{price_field: placed_price})
    # What 9.81 decodes from TWS's openOrder: the OCA link plus fields that re-encode differently.
    reported = copy.copy(placement)
    reported.ocaGroup, reported.ocaType, reported.totalQuantity = "1528474519", 3, 100.0
    for name, value in {"deltaNeutralOrderType": "None", "adjustedOrderType": "None", "settlingFirm": "?",
                        "openClose": "O", "clearingIntent": "IB", "triggerPrice": 0.0, "cashQty": 0.0,
                        "lmtPriceOffset": 0.0, "adjustedStopPrice": 0.0, "adjustedTrailingAmount": 0.0,
                        "trailStopPrice": 0.0, "volatilityType": 0, "referencePriceType": 0,
                        "discretionaryAmt": 0.0, "usePriceMgmtAlgo": False, "eTradeOnly": True}.items():
        setattr(reported, name, value)
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(live_orders={order_id: "Submitted"}, order_objects={order_id: reported},
                              positions={300: 100})
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets={"held": held})
    contract = pb.stock(300, "NASDAQ")

    modified = bridge.exit_order(held, leg, order_id, total=60, price=Decimal("49.94"))

    # The placement's encoding with only the quantity, price, OCA link and transmit flag changed.
    expected = copy.copy(placement)
    expected.totalQuantity, expected.ocaGroup, expected.ocaType, expected.transmit = 60, "1528474519", 3, True
    setattr(expected, price_field, 49.94)
    assert encode_place_order(order_id, contract, modified) == encode_place_order(order_id, contract, expected)
    assert len(encode_place_order(order_id, contract, modified)) == len(
        encode_place_order(order_id, contract, placement)
    )


def test_n4_a_capped_modify_alarms_only_when_the_gap_outlasts_the_grace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(ny(11, 0))
    held = bracket("held", "HELD", status="open", parent=10, filled=0, parent_cancel_requested=True)
    broker = connected_broker()
    for order_id, status in ((11, "Submitted"), (12, "PreSubmitted")):
        broker.orderStatus(order_id, status, 0, 0, 0)
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=clock)
    broker.execDetails(-1, None, execution("p", 10, 40, 50.0))
    broker.orderStatus(10, "Cancelled", 40, 0, 50.0)
    capsys.readouterr()
    bridge.tick()  # the position update still trails the fill
    clock.advance(2)
    broker.position(ACCOUNT, SimpleNamespace(conId=100), 40.0, 50.0)
    bridge.tick()

    # v4 raised two CRITICALs for a position update that was a moment late.
    assert [(oid, sent.totalQuantity) for oid, sent in broker.placed] == [(11, 40), (12, 40)]
    assert critical(events(capsys)) == [] and held.resized_qty == 40


    # A gap that persists (the owner sold 30 of the 40 by hand) still alarms once the grace is over.
    clock = Clock(ny(11, 0))
    _, broker, bridge = _dead_partial_entry(tmp_path / "persisting", echo=True, clock=clock)
    broker.position(ACCOUNT, SimpleNamespace(conId=100), 10.0, 50.0)
    for _ in range(7):  # 0-12 s
        bridge.tick()
        clock.advance(2)
    assert [(oid, sent.totalQuantity) for oid, sent in broker.placed] == [(11, 10), (12, 10)]
    assert critical(events(capsys)) == []
    for _ in range(3):  # 14-18 s
        bridge.tick()
        clock.advance(2)
    assert critical(events(capsys))[:2] == [
        "HELD: account holds 10 sh but the bracket expects 40 — resize of target 11 limited to 10 sh; act in TWS",
        "HELD: account holds 10 sh but the bracket expects 40 — resize of stop 12 limited to 10 sh; act in TWS",
    ]
    assert len(broker.placed) == 2  # capped totals TWS already shows are never re-sent


def test_n5_a_failed_flatten_is_never_retried_after_a_restart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(bid=None, live_orders={31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("e1", 30, 100, 50.0),), positions={300: 100})
    broker.reqOpenOrders()
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=Clock(ny(15, 45, 1)))
    capsys.readouterr()
    bridge.tick()  # no live bid and no screener snapshot: the 15:45 flatten fails
    saved = json.loads((tmp_path / "bridge" / "2026-10-13.json").read_text(encoding="utf-8"))
    assert saved["submitted"]["held"]["flatten_attempted"] is True

    broker.quote = Decimal("50.00")  # a quote exists by the time the supervisor restarts the bridge
    restored = pb.StateStore(state_dir=tmp_path / "bridge", legacy_path=None, session_date=SESSION).load()
    restarted = make_bridge(tmp_path, broker, brackets=restored, clock=Clock(ny(15, 47)))
    restarted.tick()

    assert broker.placed == []  # v4 repriced automatically after the restart
    assert critical(events(capsys)) == [
        "HELD: no quote for flatten — manual action may be needed",
        "HELD: still open after flatten (100 sh) — act in TWS",
    ]


def test_n6_no_entry_is_sent_after_a_stop_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broker = connected_broker()
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "NEWX"),))

    def quote_then_sigterm(request_id: int, contract: Any, *args: Any) -> None:
        bridge.stop_event.set()  # SIGTERM lands while the bridge waits for the pre-entry quote
        broker.tickPrice(request_id, 1, 50.10, None)

    broker.reqMktData = quote_then_sigterm  # type: ignore[method-assign]
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == [] and "d1" not in bridge.brackets  # the next run re-evaluates it
    assert ("INFO", "NEWX: stop requested — entry not sent") in events(capsys)


def test_n7_a_cancelled_parent_tws_no_longer_reports_counts_as_dead(tmp_path: Path) -> None:
    held = bracket("held", "HELD", status="open", parent=10, filled=40, parent_cancel_requested=True)
    broker = connected_broker(live_orders={11: "Submitted", 12: "PreSubmitted"},
                              executions=(execution("p", 10, 40, 50.0),), positions={100: 40})
    bridge = make_bridge(tmp_path, broker, brackets={"held": held})
    bridge.startup()  # a restart: reqOpenOrders reports the live legs, never the cancelled parent
    bridge.tick()

    assert [(order_id, sent.totalQuantity) for order_id, sent in broker.placed] == [(11, 40), (12, 40)]
    assert held.resized_qty == 40


# ---- D3: owner manual orders through the bridge -------------------------------------------------------

MANUAL_T0 = ny(10, 0)
MANUAL_CONTRACTS = {"AAPL": (265598, "NASDAQ"), "TSLA": (76792991, "NASDAQ"), "NVDA": (4815747, "NASDAQ")}
STOCK = {con_id: SimpleNamespace(conId=con_id, symbol=symbol, secType="STK", currency="USD", primaryExchange=exchange)
         for symbol, (con_id, exchange) in MANUAL_CONTRACTS.items()}


def manual_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from config import cfg
    from db.core import init_db

    path = tmp_path / "ledger.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    asyncio.run(init_db())
    return path


def queue_manual(text: str, *, message_id: int, now: datetime = MANUAL_T0, owner_shares: int = 0,
                 bot_symbols: tuple[str, ...] = ()) -> str:
    """Queue an owner order exactly as the Telegram assistant does (deterministic parser + validator)."""
    from trading.execution import manual_orders as mo

    parsed = mo.parse_order_text(text)
    assert isinstance(parsed, mo.ParsedOrder), parsed
    contract = None
    if parsed.symbol:
        con_id, exchange = MANUAL_CONTRACTS[parsed.symbol]
        contract = mo.ContractRef(symbol=parsed.symbol, con_id=con_id, primary_exchange=exchange)
    facts = mo.LiveFacts(
        now=now, accounts_all_paper=True, kill_switch_active=False, bridge_heartbeat_age_s=1.0,
        net_liquidation=Decimal("665000"),
        quote=mo.Quote(bid=Decimal("189.99"), ask=Decimal("190.01"), last=Decimal("190.00"),
                       observed_at=now - timedelta(seconds=1), real_time=True),
        owner_position_qty=Decimal(owner_shares), open_manual_sell_qty=0, resting_stop_qty=Decimal("0"),
        bot_symbols=frozenset(bot_symbols), manual_open_buy_notional_today=Decimal("0"),
        recent_manual_orders_10min=0,
    )
    message_date = now - timedelta(seconds=1)
    validated = mo.validate(parsed, facts=facts, contract=contract, message_date=message_date)
    assert isinstance(validated, mo.ValidatedOrder), validated
    return asyncio.run(mo.queue_order(
        validated, chat_id=7, message_id=message_id, update_id=10_000 + message_id, source=parsed.source,
        raw_text=text, message_date=message_date, audit={"test": True}, now=now + timedelta(microseconds=message_id),
    ))


def manual_row(row_id: str) -> Any:
    from trading.execution import manual_orders as mo

    return asyncio.run(mo.get_order(row_id))


def desk_bridge(tmp_path: Path, broker: FakeBroker, ledger: Path, **kwargs: Any) -> Any:
    bridge = make_bridge(tmp_path, broker, db=ledger, **kwargs)
    desk = pb.manual_desk(bridge)
    assert desk is not None
    bridge.attach_manual_desk(desk)
    return bridge


def manual_lines(out: list[tuple[str, str]]) -> list[str]:
    return [text for kind, text in out if kind == "MANUAL"]


def fill(broker: FakeBroker, order_id: int, shares: int, price: float, ref: str, exec_id: str) -> None:
    broker.execDetails(-1, None, execution(exec_id, order_id, shares, price, acctNumber=ACCOUNT, orderRef=ref,
                                           time="20261013  10:00:30"))
    broker.orderStatus(order_id, "Filled", shares, 0, price)


def test_d3_a_market_buy_is_claimed_sent_and_followed_to_filled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.execution import manual_orders as mo

    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    capsys.readouterr()
    bridge.tick()

    # Rewritten for v7c (explicit, not weakened; D5): a buy leaves as two orders, the DAY entry with
    # transmit=False and its attached GTC stop, whose transmit=True sends both (the bot's bracket rule).
    # Every assertion on the entry is kept; the stop itself is pinned by test_v7c_manual_buy_sends_attached_stop.
    [(order_id, sent), (stop_id, stop)] = broker.placed
    assert (sent.action, sent.orderType, sent.totalQuantity, sent.tif, sent.transmit, sent.account) == (
        "BUY", "MKT", 10, "DAY", False, ACCOUNT
    )
    assert (stop_id, stop.parentId, stop.transmit) == (order_id + 1, order_id, True)
    assert (sent.orderRef, sent.eTradeOnly, sent.firmQuoteOnly) == (f"manual:{row_id}", False, False)
    assert (manual_row(row_id).status, manual_row(row_id).ibkr_order_id) == ("sent", order_id)
    assert asyncio.run(mo.read_bridge_heartbeat()).updated_at == clock.value

    fill(broker, order_id, 10, 190.01, f"manual:{row_id}", "0000e0d5.000000a1.01.01")
    clock.advance(2)
    bridge.tick()
    row = manual_row(row_id)
    assert (row.status, row.filled_qty, row.avg_fill_price) == ("filled", 10, Decimal("190.0100"))
    assert asyncio.run(mo.read_bridge_heartbeat()).updated_at == clock.value  # written every tick
    out = events(capsys)
    assert manual_lines(out) == [  # v7c: the SENT line names the attached stop and its order too
        f"SENT BUY 10 AAPL MKT #{row_id[:6]} (order {order_id}) with its stop 185 GTC (order {stop_id})",
        f"FILLED BUY 10 AAPL MKT @ 190.0100 #{row_id[:6]}",
    ]
    assert not [kind for kind, _ in out if kind in {"SENT", "FILL", "EXIT", "CRITICAL"}]  # the notifier skips MANUAL


# Rewritten for v7c (explicit, not weakened): the buy case left this table. A buy is no longer a plain
# DAY order but a DAY entry plus its attached GTC stop, and with 100 AAPL held and no stop covering
# them, v7c refuses every buy (v7c_unprotected_position). Its limit entry keeps every assertion this
# table made, as a bracket, in test_v7c_manual_buy_sends_attached_stop.
@pytest.mark.parametrize(("text", "expected"), [
    ("/sell 30 AAPL", ("SELL", "MKT", 30, None, None)),
    ("/sell 30 AAPL 191", ("SELL", "LMT", 30, "lmtPrice", 191.0)),
    ("/stop 50 AAPL at 185", ("SELL", "STP", 50, "auxPrice", 185.0)),
    ("/close AAPL", ("SELL", "MKT", 100, None, None)),  # the owner's whole unreserved holding
])
def test_d3_every_owner_order_type_is_sent_as_a_plain_day_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, expected: tuple[Any, ...]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual(text, message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={265598: 100})
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    [(_, sent)] = broker.placed
    action, order_type, quantity, price_field, price = expected
    assert (sent.action, sent.orderType, sent.totalQuantity, sent.tif, sent.parentId) == (
        action, order_type, quantity, "DAY", 0
    )  # never a bracket
    if price_field is not None:
        assert getattr(sent, price_field) == pytest.approx(price)
    assert manual_row(row_id).status == "sent"


def test_d3_a_stop_the_owner_placed_by_hand_in_tws_reserves_shares_at_send_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/sell 100 AAPL", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={265598: 100})
    # After the order was queued the owner put a stop for 60 shares in TWS by hand (client 0, no orderRef).
    stop = pb.order("SELL", "STP", 60, ACCOUNT, "", auxPrice=180.0, tif="GTC")
    stop.clientId, stop.permId = 0, 990001
    broker.book.append((0, SimpleNamespace(conId=265598, symbol="AAPL"), stop, "PreSubmitted", 60.0))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []  # 100 more would have left the account short when the stop fires
    row = manual_row(row_id)
    assert (row.status, row.reject_code) == ("rejected", "insufficient_shares")
    [line] = manual_lines(events(capsys))
    assert line.startswith(f"REJECTED SELL 100 AAPL MKT #{row_id[:6]}: Can't sell 100 AAPL: 40 available")
    assert line.endswith("(insufficient_shares)")


def test_d3_a_row_claimed_before_a_crash_is_marked_unknown_and_never_resent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.execution import manual_orders as mo

    ledger = manual_ledger(tmp_path, monkeypatch)
    lost = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    known = queue_manual("/buy 5 TSLA stop 185", message_id=2)
    for _ in (lost, known):  # both claimed by the previous bridge, which then died
        assert asyncio.run(mo.claim_next_order(MANUAL_T0 + timedelta(seconds=1))) is not None
    # Only the TSLA order reached IBKR before the crash: TWS still reports it.
    reported = pb.order("BUY", "MKT", 5, ACCOUNT, f"manual:{known}", tif="DAY")
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), live_orders={77: "Submitted"},
                              order_objects={77: reported})
    clock = Clock(MANUAL_T0 + timedelta(seconds=20))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    capsys.readouterr()
    bridge.startup()
    for _ in range(3):
        bridge.tick()
        clock.advance(2)

    assert broker.placed == []  # never resent
    assert manual_row(lost).status == "unknown"
    assert (manual_row(known).status, manual_row(known).ibkr_order_id) == ("sent", 77)
    out = events(capsys)
    assert critical(out) == [
        f"manual order #{lost[:6]} was being sent when the bridge stopped and IBKR has no order for it — marked"
        " unknown and never resent; check TWS"
    ]  # TSLA's live order 77 is the owner's, not an unknown bot order
    assert manual_lines(out) == [f"SENT BUY 5 TSLA MKT #{known[:6]} (orders [77])"]


def test_d3_cancel_requests_cancel_only_that_rows_order_and_settle_on_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL 185 stop 180", message_id=1)
    other = queue_manual("/buy 3 TSLA 185 stop 180", message_id=2)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    bridge.tick()
    # Rewritten for v7c (explicit, not weakened): each buy is its entry plus its attached stop (D5).
    order_id, stop_id, other_id, other_stop = (order_id for order_id, _ in broker.placed)
    assert (stop_id, other_stop) == (order_id + 1, other_id + 1)

    queue_manual(f"/cancel {row_id[:6]}", message_id=3, now=clock.value)
    capsys.readouterr()
    bridge.tick()
    bridge.tick()
    assert broker.cancelled == [order_id]  # once, and only that row's own entry: never its stop
    broker.orderStatus(order_id, "Cancelled", 0, 10, 0)  # IBKR confirms
    bridge.tick()

    assert manual_row(row_id).status == "cancelled"
    assert manual_row(other).status == "sent" and other_id not in broker.cancelled
    assert manual_lines(events(capsys)) == [
        f"CANCEL BUY 10 AAPL LMT 185 #{row_id[:6]} (order {order_id})",
        f"CANCELLED BUY 10 AAPL LMT 185 #{row_id[:6]} (filled 0)",
    ]


def test_d3_a_tws_rejection_marks_the_row_rejected_with_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    # Rewritten for v7c (explicit, not weakened): the buy's attached stop is the second order (D5).
    [(order_id, _), (stop_id, _)] = broker.placed
    assert stop_id == order_id + 1
    capsys.readouterr()
    broker.error(order_id, 201, "Order rejected - reason: no trading permissions")
    broker.orderStatus(order_id, "Inactive", 0, 10, 0)
    clock.advance(2)
    bridge.tick()

    row = manual_row(row_id)
    assert (row.status, row.reject_code) == ("rejected", "tws_201")
    out = events(capsys)
    assert critical(out) == [f"TWS {order_id} 201 Order rejected - reason: no trading permissions"]
    assert manual_lines(out) == [
        f"REJECTED BUY 10 AAPL MKT #{row_id[:6]}: Order rejected - reason: no trading permissions (tws_201)"
    ]


def test_d3_close_of_a_bot_symbol_reprices_the_bracket_target_any_time_of_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    held = bracket("bot", "AAPL", status="open", parent=30, filled=100)
    held.con_id = 265598
    clock = Clock(ny(11, 0, 2))
    row_id = queue_manual("/close AAPL", message_id=1, now=ny(11, 0), bot_symbols=("AAPL",))
    broker = connected_broker(bid=Decimal("50.20"), ask=Decimal("50.22"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("e1", 30, 100, 50.0),), positions={265598: 100})
    broker.reqOpenOrders()
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"bot": held}, clock=clock)
    capsys.readouterr()
    bridge.tick()

    # The bracket's own target is repriced once: never a second sell next to the bot's legs.
    [(order_id, sent)] = broker.placed
    assert (order_id, sent.action, sent.orderType, sent.totalQuantity) == (31, "SELL", "LMT", 100)
    assert held.flatten_sent and manual_row(row_id).status == "sent"
    fill(broker, 31, 100, 49.94, "autotrader:bot", "0000e0d5.000000b1.01.01")
    clock.advance(2)
    bridge.tick()
    assert held.status == "closed"
    row = manual_row(row_id)
    assert (row.status, row.filled_qty, row.avg_fill_price) == ("filled", 100, Decimal("49.9400"))
    out = events(capsys)
    assert ("FLATTEN", "AAPL: target repriced to 49.94 for 100 sh (bid 50.2)") in out
    assert manual_lines(out)[:1] == [f"CLOSE AAPL #{row_id[:6]}: bot bracket closing"]
    assert f"FILLED CLOSE AAPL #{row_id[:6]}: bot bracket closed, P&L -6.00" in manual_lines(out)


def test_d3_close_all_flattens_bot_brackets_and_sells_only_unreserved_owner_longs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    bot = bracket("bot", "NVDA", status="open", parent=30, filled=100)
    bot.con_id = 4815747
    row_id = queue_manual("/closeall", message_id=1)
    option = SimpleNamespace(conId=555, symbol="AAPL", secType="OPT", currency="USD", primaryExchange="")
    broker = connected_broker(
        bid=Decimal("189.99"), ask=Decimal("190.01"), live_orders={31: "Submitted", 32: "PreSubmitted"},
        executions=(execution("e1", 30, 100, 50.0),),
        positions={4815747: 100, 76792991: 50, 265598: 20, 555: 3},
        contracts={**STOCK, 555: option},
    )
    stop = pb.order("SELL", "STP", 20, ACCOUNT, "", auxPrice=180.0, tif="GTC")  # the owner's own AAPL stop
    stop.clientId, stop.permId = 0, 990002
    broker.book.append((0, SimpleNamespace(conId=265598, symbol="AAPL"), stop, "Submitted", 20.0))
    broker.reqOpenOrders()
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"bot": bot}, clock=clock)
    capsys.readouterr()
    bridge.tick()

    [(target, repriced), (child, sell)] = broker.placed
    assert (target, repriced.action, repriced.totalQuantity) == (31, "SELL", 100)  # the bot's own target
    assert (sell.action, sell.orderType, sell.totalQuantity, sell.orderRef) == ("SELL", "MKT", 50, f"manual:{row_id}")
    assert manual_row(row_id).status == "sent"
    fill(broker, child, 50, 189.99, f"manual:{row_id}", "0000e0d5.000000c1.01.01")
    clock.advance(2)
    bridge.tick()
    assert manual_row(row_id).status == "partially_filled"  # F4: the bot's NVDA close is still working
    fill(broker, 31, 100, 189.9, "autotrader:bot", "0000e0d5.000000c2.01.01")
    clock.advance(2)
    bridge.tick()
    bridge.tick()
    skipped = ("AAPL: OPT/USD is not a US stock — not sold; AAPL: 20 of 20 reserved by working sells/stops — cancel"
               " them first — not sold")
    assert (manual_row(row_id).status, manual_row(row_id).error) == ("cancelled", skipped)  # R3: AAPL kept back
    closing = [line for line in manual_lines(events(capsys)) if line.startswith("CLOSEALL")]
    assert closing == [f"CLOSEALL #{row_id[:6]}: 1 sell order(s), 1 bot close(s) — sent; {skipped}"]


def test_d3_one_bad_row_never_blocks_the_flatten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1, now=ny(15, 44, 50))
    broker = connected_broker(bid=Decimal("50.00"), ask=Decimal("50.02"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              positions={300: 100})
    broker.reqOpenOrders()
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"held": held}, clock=Clock(ny(15, 45, 5)))

    def broken(row: Any) -> None:
        raise ValueError("corrupt row")

    bridge.manual.process = broken
    capsys.readouterr()
    bridge.tick()
    bridge.tick()

    assert [order_id for order_id, _ in broker.placed] == [31]  # the bot's flatten went out
    assert manual_row(row_id).status == "error"  # never reached IBKR, never retried
    assert critical(events(capsys)) == [
        f"manual order #{row_id[:6]} (BUY 10 AAPL MKT) failed while being sent (ValueError: corrupt row) — marked"
        " error, never resent; check TWS"
    ]


def test_d3_manual_orders_never_take_bot_slots_and_keep_bot_entries_off_their_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    queue_manual("/buy 10 AAPL 185 stop 180", message_id=1)
    queue_manual("/buy 5 TSLA 185 stop 180", message_id=2)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    bridge.tick()
    # Rewritten for v7c (explicit, not weakened): two buys are four orders, each entry and its attached stop.
    assert [sent.action for _, sent in broker.placed] == ["BUY", "SELL", "BUY", "SELL"]
    bridge.decisions = lambda: [decision_row("aapl", "AAPL", created=clock.value, con_id=265598),
                                decision_row("new", "NEWX", created=clock.value)]
    bridge.tick()

    assert bridge.brackets["aapl"].skip_reason == "symbol has a working manual order"
    assert bridge.brackets["new"].status == "sent"  # two working manual orders took no bot slot


def test_d3_owner_orders_stay_off_when_the_ledger_is_another_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manual_ledger(tmp_path, monkeypatch)
    bridge = make_bridge(tmp_path, connected_broker(), db=tmp_path / "other.db")
    capsys.readouterr()
    assert pb.manual_desk(bridge) is None
    [line] = critical(events(capsys))
    assert line.endswith("— owner orders disabled")


# ---- half days: every checkpoint follows the real XNYS close --------------------------------------------


def test_half_day_checkpoints_follow_the_1300_close(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    half_day = date(2026, 11, 27)
    times = pb.session_times(half_day)
    assert (times.closes_at, times.last_entry, times.flatten, times.verify, times.stop) == tuple(
        datetime(2026, 11, 27, hour, minute, tzinfo=pb.NY) for hour, minute in
        ((13, 0), (12, 30), (12, 45), (12, 50), (12, 55))
    )
    assert pb.session_times(date(2026, 12, 24)).flatten == datetime(2026, 12, 24, 12, 45, tzinfo=pb.NY)
    assert pb.session_times(SESSION).flatten == ny(15, 45)

    clock = Clock(datetime(2026, 11, 27, 12, 44, 30, tzinfo=pb.NY))
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(bid=Decimal("50.20"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("e1", 30, 100, 50.0),), positions={300: 100})
    bridge = make_bridge(tmp_path, broker, brackets={"held": held}, clock=clock, session_date=half_day)
    bridge.decisions = lambda: [decision_row("late", "LATE", created=clock.value)]
    bridge.stop_event = SteppingStop(clock, 30)
    capsys.readouterr()

    assert bridge.run() == pb.EXIT_OK

    assert [order_id for order_id, _ in broker.placed] == [31]  # the 12:45 flatten
    out = events(capsys)
    assert ("INFO", "12:45 ET: flattening all engine positions") in out
    assert critical(out) == ["HELD: still open after flatten (100 sh) — act in TWS"] * 3  # 12:46, 12:50, 12:58
    assert bridge.brackets["late"].skip_reason == "after 12:30 — no new entries"
    assert clock.value >= datetime(2026, 11, 27, 12, 58, tzinfo=pb.NY)


def test_d3_cancel_all_cancels_every_working_owner_order_and_keeps_partial_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    first = queue_manual("/buy 10 AAPL 185 stop 180", message_id=1)
    second = queue_manual("/buy 3 TSLA 185 stop 180", message_id=2)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    bridge.tick()
    # Rewritten for v7c (explicit, not weakened): each buy is its entry plus its attached stop (D5),
    # and /cancel all cancels only the entries: the stops keep protecting the 4 shares bought.
    first_id, first_stop, second_id, second_stop = (order_id for order_id, _ in broker.placed)
    assert (first_stop, second_stop) == (first_id + 1, second_id + 1)
    broker.execDetails(-1, None, execution("0000e0d5.000000d1.01.01", first_id, 4, 185.0, acctNumber=ACCOUNT,
                                           orderRef=f"manual:{first}", time="20261013  10:00:30"))
    broker.orderStatus(first_id, "Submitted", 4, 6, 185.0)
    bridge.tick()
    assert (manual_row(first).status, manual_row(first).filled_qty) == ("partially_filled", 4)

    queue_manual("/cancel all", message_id=3, now=clock.value)
    capsys.readouterr()
    bridge.tick()
    assert sorted(broker.cancelled) == sorted([first_id, second_id])
    broker.orderStatus(first_id, "Cancelled", 4, 6, 185.0)
    broker.orderStatus(second_id, "Cancelled", 0, 3, 0)
    bridge.tick()

    assert (manual_row(first).status, manual_row(first).filled_qty) == ("cancelled", 4)
    assert (manual_row(second).status, manual_row(second).filled_qty) == ("cancelled", 0)
    assert f"CANCELLED BUY 10 AAPL LMT 185 #{first[:6]} (filled 4)" in manual_lines(events(capsys))


def test_d3_an_order_that_filled_while_the_bridge_was_down_is_recorded_filled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.execution import manual_orders as mo

    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    assert asyncio.run(mo.claim_next_order(MANUAL_T0 + timedelta(seconds=1))) is not None
    # The previous bridge sent it as order 88 and died; it filled meanwhile and is no longer open.
    filled = execution("0000e0d5.000000e1.01.01", 88, 10, 190.0, acctNumber=ACCOUNT, orderRef=f"manual:{row_id}",
                       time="20261013  10:00:05")
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), executions=(filled,))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=30)))
    capsys.readouterr()
    bridge.startup()
    bridge.tick()

    assert broker.placed == []
    row = manual_row(row_id)
    assert (row.status, row.filled_qty, row.ibkr_order_id) == ("filled", 10, 88)
    assert critical(events(capsys)) == []


def test_d3_another_clients_order_with_our_order_id_never_overwrites_our_leg(tmp_path: Path) -> None:
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    ours = pb.order("SELL", "LMT", 100, ACCOUNT, "autotrader:held", lmtPrice=51.0, tif="GTC", parentId=30)
    broker = connected_broker(live_orders={31: "Submitted", 32: "PreSubmitted"}, order_objects={31: ours},
                              positions={300: 100})
    broker.reqOpenOrders()
    theirs = pb.order("BUY", "LMT", 7, ACCOUNT, "other-app", lmtPrice=10.0)
    theirs.clientId, theirs.permId = 5, 990003  # another API client numbers its orders from 1 too
    broker.book.append((31, SimpleNamespace(conId=999, symbol="ZZZ"), theirs, "Cancelled", 0.0))
    make_bridge(tmp_path, broker, brackets={"held": held})
    broker.reqAllOpenOrders()

    assert broker.order_status[31] == "Submitted"
    assert broker.order_objects[31].action == "SELL" and broker.open_orders[31].status == "Submitted"
    assert any(item.client_id == 5 for item in broker.account_orders.values())


def test_d3_close_all_sends_nothing_more_once_a_stop_is_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/closeall", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={76792991: 50, 4815747: 20},
                              contracts=STOCK)
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    original = broker.reqMktData

    def quote_then_sigterm(request_id: int, contract: Any, *args: Any) -> None:
        original(request_id, contract, *args)
        bridge.stop_event.set()  # SIGTERM lands during the first child's quote wait

    broker.reqMktData = quote_then_sigterm  # type: ignore[method-assign]
    bridge.tick()

    assert broker.placed == []
    assert manual_row(row_id).status == "error"  # never reached IBKR: the owner's longs are still open


# ---- final review 2026-10-13: F1-F4 and L-1..L-4 ----------------------------------------------------------

AAPL_ID, TSLA_ID = 265598, 76792991


def _bot_leg(order_id: int, kind: str, *, parent: int, ref: str) -> Any:
    """A GTC exit leg the bridge sent (client 18) that is still live at IBKR."""
    if kind == "LMT":
        leg = pb.order("SELL", "LMT", 100, ACCOUNT, ref, lmtPrice=200.0, tif="GTC", parentId=parent)
    else:
        leg = pb.order("SELL", "STP", 100, ACCOUNT, ref, auxPrice=180.0, tif="GTC", parentId=parent)
    leg.clientId, leg.permId = CLIENT_ID, 880000 + order_id
    return leg


def _broker_with_live_bot_legs(target: int, stop: int, *, parent: int, ref: str) -> FakeBroker:
    legs = {target: _bot_leg(target, "LMT", parent=parent, ref=ref), stop: _bot_leg(stop, "STP", parent=parent, ref=ref)}
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK,
                              live_orders={target: "Submitted", stop: "PreSubmitted"}, order_objects=legs)
    contract = SimpleNamespace(conId=AAPL_ID, symbol="AAPL")
    broker.open_order_contracts = {target: contract, stop: contract}  # as TWS reports them to client 18
    broker.book += [(target, contract, legs[target], "Submitted", 100.0),
                    (stop, contract, legs[stop], "PreSubmitted", 100.0)]
    return broker


def manual_sells(broker: FakeBroker, row_id: str) -> list[Any]:
    return [sent for _, sent in broker.placed if sent.orderRef == f"manual:{row_id}"]


@pytest.mark.parametrize("text", ["/close AAPL", "/sell 100 AAPL", "/sell all AAPL", "/buy 10 AAPL stop 185"])
def test_f1_a_past_sessions_live_bot_legs_keep_owner_sells_off_their_shares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    # Yesterday's bracket was not flat at its close: 100 AAPL and its GTC target + stop are still live at IBKR.
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual(text, message_id=1, owner_shares=100)  # Telegram only knows today's bridge state
    broker = _broker_with_live_bot_legs(41, 42, parent=40, ref="autotrader:yesterday0001")
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.startup()
    bridge.tick()

    # v5 sold the 100 at market; a live leg would then sell 100 more and leave the account short.
    assert manual_sells(broker, row_id) == []
    row = manual_row(row_id)
    assert (row.status, row.reject_code) == ("rejected", "bot_holds_symbol")
    # L-d: no bracket today to close: the owner is sent to TWS, not told "only /close AAPL".
    assert row.error == ("The bot still has orders working on AAPL at IBKR from an earlier session; act in TWS."
                         " Nothing sent.")


def test_f1_closeall_leaves_a_symbol_with_live_bot_legs_to_the_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/closeall", message_id=1)
    broker = _broker_with_live_bot_legs(41, 42, parent=40, ref="autotrader:yesterday0001")
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.startup()
    bridge.tick()

    assert manual_sells(broker, row_id) == []
    row = manual_row(row_id)
    assert (row.status, row.reject_code) == ("rejected", "nothing_closed")
    assert row.error == "AAPL: the bot's orders still work at IBKR — not sold; act in TWS"


def test_f1_a_dangling_leg_of_a_finished_bracket_keeps_owner_sells_off_its_shares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    finished = bracket("bot", "AAPL", status="cancelled", parent=30)  # per state the entry never filled
    finished.con_id = AAPL_ID
    broker = _broker_with_live_bot_legs(31, 32, parent=30, ref="autotrader:bot")
    row_id = queue_manual("/sell 100 AAPL", message_id=1, owner_shares=100)
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"bot": finished},
                         clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.startup()
    bridge.tick()

    assert manual_sells(broker, row_id) == []
    assert manual_row(row_id).reject_code == "bot_holds_symbol"


def test_f1_every_working_sell_reserves_shares_the_bots_own_legs_included(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    desk = desk_bridge(tmp_path, connected_broker(), ledger).manual
    leg = pb.AccountOrder(client_id=CLIENT_ID, order_id=41, perm_id=1, account=ACCOUNT, con_id=AAPL_ID, symbol="AAPL",
                          action="SELL", order_type="STP", order_ref="autotrader:yesterday0001",
                          total=Decimal("100"), status="PreSubmitted")
    owners = replace(leg, client_id=0, order_ref="", perm_id=2)

    assert desk.reserves_shares(leg, AAPL_ID, frozenset())
    assert desk.reserves_shares(owners, AAPL_ID, frozenset())
    assert not desk.reserves_shares(replace(leg, action="BUY"), AAPL_ID, frozenset())
    assert not desk.reserves_shares(replace(leg, status="Filled"), AAPL_ID, frozenset())


@pytest.mark.parametrize("second", ["/close AAPL", "/sell 100 AAPL"])
def test_f2_a_filled_sell_keeps_its_shares_until_a_position_update_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, second: str
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    first = queue_manual("/sell 100 AAPL", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100})
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(first_id, _)] = broker.placed
    fill(broker, first_id, 100, 189.99, f"manual:{first}", "0000e0d5.0000f001.01.01")
    clock.advance(2)
    bridge.tick()
    assert manual_row(first).status == "filled"

    # reqPositions has not delivered AAPL 100 -> 0 yet when the owner's next sell arrives.
    second_id = queue_manual(second, message_id=2, now=clock.value, owner_shares=100)
    clock.advance(2)
    bridge.tick()

    assert [sent.totalQuantity for _, sent in broker.placed] == [100]  # v5 sold 100 again: short 100
    assert (manual_row(second_id).status, manual_row(second_id).reject_code) == ("rejected", "insufficient_shares")


def test_f2_the_reservation_ends_when_a_position_update_shows_the_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    first = queue_manual("/sell 100 AAPL", message_id=1, owner_shares=200)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 200})
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(first_id, _)] = broker.placed
    fill(broker, first_id, 100, 189.99, f"manual:{first}", "0000e0d5.0000f002.01.01")
    clock.advance(2)
    bridge.tick()
    too_many = queue_manual("/sell 150 AAPL", message_id=2, now=clock.value, owner_shares=200)
    clock.advance(1)
    bridge.tick()
    assert manual_row(too_many).reject_code == "insufficient_shares"  # 200 shown, 100 already sold

    broker.position(ACCOUNT, STOCK[AAPL_ID], 100.0, 189.99)  # the position catches up: 100 left
    rest = queue_manual("/sell 100 AAPL", message_id=3, now=clock.value, owner_shares=100)
    clock.advance(1)
    bridge.tick()
    assert manual_row(rest).status == "sent"  # counted once: never subtracted twice


class SlowStatusBroker(FakeBroker):
    """TWS answers a new order with a warning first; its openOrder and orderStatus come later."""

    def placeOrder(self, order_id: int, contract: Any, order: Any) -> None:
        self.placed.append((order_id, order))


def slow_status_broker(**kwargs: Any) -> SlowStatusBroker:
    broker = SlowStatusBroker(**kwargs)
    broker.connect("127.0.0.1", 7497, CLIENT_ID)
    broker.reqAccountSummary(1, "All", "NetLiquidation")
    broker.reqPositions()
    return broker


def test_f3_a_tws_warning_is_noted_and_the_order_still_followed_to_its_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/sell 100 AAPL", message_id=1, owner_shares=100)
    broker = slow_status_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100})
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(order_id, sent)] = broker.placed
    broker.error(order_id, 10349, "Order TIF was set to DAY based on order preset.")
    for _ in range(3):
        clock.advance(2)
        bridge.tick()
    row = manual_row(row_id)
    assert row.status == "sent"  # v5 recorded "rejected" (final) and stopped following it
    assert {"tws_warning": "10349 Order TIF was set to DAY based on order preset.", "order": order_id} in [
        event.get("detail") for event in row.audit["events"]
    ]

    echo = copy.copy(sent)
    echo.clientId = CLIENT_ID
    broker.openOrder(order_id, SimpleNamespace(symbol="AAPL", conId=AAPL_ID), echo, SimpleNamespace(status="Submitted"))
    broker.orderStatus(order_id, "Submitted", 0, 100, 0)
    fill(broker, order_id, 100, 189.99, f"manual:{row_id}", "0000e0d5.0000a001.01.01")
    clock.advance(2)
    bridge.tick()
    assert (manual_row(row_id).status, manual_row(row_id).filled_qty) == ("filled", 100)
    assert f"WARNING SELL 100 AAPL MKT #{row_id[:6]}: TWS 10349 Order TIF was set to DAY based on order preset." \
        f" (order {order_id})" in manual_lines(events(capsys))


def test_f4_a_closeall_that_could_close_nothing_is_rejected_with_the_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/closeall", message_id=1)
    broker = connected_broker(bid=Decimal("186.00"), ask=Decimal("190.00"), positions={TSLA_ID: 50, AAPL_ID: 20},
                              contracts=STOCK)  # a 2% wide book: no market sell is allowed
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    assert broker.placed == []
    row = manual_row(row_id)  # v5 said "Filled CLOSE ALL" while 70 shares were still held
    assert (row.status, row.reject_code) == ("rejected", "nothing_closed")
    assert row.error == "AAPL: spread 2.13% over 1% — not sold; TSLA: spread 2.13% over 1% — not sold"


def test_f4_a_closeall_with_nothing_held_is_filled_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/closeall", message_id=1)
    bridge = desk_bridge(tmp_path, connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01")), ledger,
                         clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    assert manual_row(row_id).status == "filled"


@pytest.mark.parametrize(("problem", "note"), [
    ("wide", "TSLA: spread 2.13% over 1% — not sold"),
    ("delayed", "TSLA: only a delayed quote — not sold"),
    ("short", "TSLA: short 50 (sells only) — not sold"),
])
def test_l1_closeall_sells_only_long_positions_at_a_tight_real_time_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str, note: str
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/closeall", message_id=1)
    bid, ask = (Decimal("186.00"), Decimal("190.00")) if problem == "wide" else (Decimal("189.99"), Decimal("190.01"))
    broker = connected_broker(bid=bid, ask=ask, positions={TSLA_ID: -50 if problem == "short" else 50}, contracts=STOCK)
    if problem == "delayed":
        def delayed(request_id: int, contract: Any, *args: Any) -> None:
            broker.tickPrice(request_id, 66, 189.99, None)
            broker.tickPrice(request_id, 67, 190.01, None)

        broker.reqMktData = delayed  # type: ignore[method-assign]
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    assert broker.placed == []
    assert (manual_row(row_id).status, manual_row(row_id).error) == ("rejected", note)


@pytest.mark.parametrize("fence", ["on", "unreadable"])
def test_l1_a_manual_buy_meets_the_kill_switch_at_send_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fence: str
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)  # queued while the fence was off
    bridge = desk_bridge(tmp_path, connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01")), ledger,
                         clock=Clock(MANUAL_T0 + timedelta(seconds=2)))

    def unreadable() -> bool:
        raise sqlite3.OperationalError("database is locked")

    bridge.kill_switch_active = (lambda: True) if fence == "on" else unreadable
    bridge.tick()

    assert bridge.app.placed == []
    assert manual_row(row_id).reject_code == "kill_switch"


def test_l1_a_delayed_quote_never_counts_as_real_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    broker = connected_broker()

    def delayed(request_id: int, contract: Any, *args: Any) -> None:
        broker.tickPrice(request_id, 66, 189.99, None)
        broker.tickPrice(request_id, 67, 190.01, None)

    broker.reqMktData = delayed  # type: ignore[method-assign]
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    assert broker.placed == []
    row = manual_row(row_id)
    assert (row.reject_code, row.error) == ("no_fresh_quote", "Only a delayed quote is available. Nothing sent.")


def test_l1_owner_orders_are_refused_when_tws_reports_a_non_paper_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/sell 10 AAPL", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100})
    broker.managedAccounts(f"{ACCOUNT},U7654321")  # a live account appeared in this TWS
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    assert broker.placed == []
    assert manual_row(row_id).reject_code == "not_paper"


def test_l1_no_owner_order_is_sent_after_a_stop_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    original = broker.reqMktData

    def quote_then_sigterm(request_id: int, contract: Any, *args: Any) -> None:
        original(request_id, contract, *args)
        bridge.stop_event.set()  # SIGTERM during the send-time quote wait

    broker.reqMktData = quote_then_sigterm  # type: ignore[method-assign]
    bridge.tick()

    assert broker.placed == []
    assert manual_row(row_id).status == "error"  # certainly never reached IBKR


def test_l1_a_failure_after_place_order_is_unknown_never_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    original = broker.placeOrder

    def placed_then_socket_died(order_id: int, contract: Any, order: Any) -> None:
        original(order_id, contract, order)
        raise OSError("socket closed after the order was written")

    broker.placeOrder = placed_then_socket_died  # type: ignore[method-assign]
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    assert manual_row(row_id).status == "unknown"  # it may be live at IBKR: never "error", never resent
    assert any("marked unknown, never resent" in text for text in critical(events(capsys)))


def test_l1_the_state_is_saved_before_any_entry_order_leaves(tmp_path: Path) -> None:
    broker = connected_broker(bid=Decimal("50.10"))
    saved_at_send: list[dict[str, Any]] = []
    original = broker.placeOrder

    def place(order_id: int, contract: Any, order: Any) -> None:
        state = tmp_path / "bridge" / "2026-10-13.json"
        saved_at_send.append(json.loads(state.read_text(encoding="utf-8"))["submitted"] if state.exists() else {})
        original(order_id, contract, order)

    broker.placeOrder = place  # type: ignore[method-assign]
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL"),))
    bridge.tick()

    assert len(saved_at_send) == 3
    assert all(state["d1"]["status"] == "sent" and state["d1"]["parent"] == 100 for state in saved_at_send)


def test_l1_the_daily_loss_stop_blocks_new_entries(tmp_path: Path) -> None:
    loss = bracket("lost", "LOST", status="closed", parent=60, filled=100, exit_filled=100, realized_pnl="-13300.00")
    bridge = make_bridge(tmp_path, connected_broker(bid=Decimal("50.10")), brackets={"lost": loss},
                         decisions=(decision_row("d1", "AAPL"),))
    bridge.tick()

    assert bridge.app.placed == []
    # Rewritten for v7c (explicit, not weakened): the skip reason now starts with its token and says the
    # loss is the account's (O14); it keeps the phrase the notifier and session_check match on.
    assert bridge.brackets["d1"].skip_reason == (
        "daily_loss_limit: daily loss limit reached (account realized loss 13300.00 >= 2% of net liquidation"
        " 665000.00)"
    )  # 2% of 665,000


def test_l1_the_configured_account_must_be_managed_by_this_tws(
    bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    broker = FakeBroker(accounts="DU7654321")
    broker.disconnect_after = 3  # fail fast instead of running if the refusal is missing
    bridge_env.install(broker, Clock(ny(10, 0)))

    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_REFUSED

    assert ("CRITICAL", "refusing: configured paper account not managed by this TWS") in events(capsys)
    assert broker.placed == [] and broker.connected is False


def test_l1_a_queued_row_with_a_cancel_request_is_never_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    with closing(sqlite3.connect(ledger)) as connection:  # a cancel flag that raced the claim
        connection.execute("UPDATE autotrader_manual_orders SET cancel_requested=1 WHERE id=?", (row_id,))
        connection.commit()
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    assert broker.placed == [] and manual_row(row_id).status == "queued"


def test_l1_unknown_manual_buys_still_count_toward_the_manual_exposure_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading.execution import manual_orders as mo

    ledger = manual_ledger(tmp_path, monkeypatch)
    for message_id, text in ((1, "/buy 800 AAPL stop 185"), (2, "/buy 800 TSLA stop 185")):  # ~152,000 each
        queue_manual(text, message_id=message_id)
        assert asyncio.run(mo.claim_next_order(MANUAL_T0 + timedelta(seconds=1))) is not None
    asyncio.run(mo.mark_unknown_sending_rows(set(), now=MANUAL_T0 + timedelta(seconds=1)))  # both unknown
    row_id = queue_manual("/buy 200 NVDA stop 185", message_id=3)  # +38,000: over 50% of 665,000 with the two above
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    assert broker.placed == []
    assert manual_row(row_id).reject_code == "manual_exposure_cap"


def test_l1_the_audit_records_the_owners_shares_without_the_bots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    held = bracket("bot", "AAPL", status="open", parent=30, filled=100)
    held.con_id = AAPL_ID
    row_id = queue_manual("/sell 50 AAPL", message_id=1, owner_shares=50)  # the owner bought 50 by hand later
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 150},
                              live_orders={31: "Submitted", 32: "PreSubmitted"})
    broker.reqOpenOrders()
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"bot": held}, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    row = manual_row(row_id)
    assert row.reject_code == "bot_holds_symbol"
    owner = Decimal(row.audit["events"][-1]["detail"]["facts"]["owner_position_qty"])
    assert owner == 50  # 150 held, 100 of them are the bot's


def test_l2_earlier_session_rows_ibkr_no_longer_works_are_settled_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.execution import manual_orders as mo

    ledger = manual_ledger(tmp_path, monkeypatch)
    yesterday = ny(10, 0) - timedelta(days=1)
    partial = queue_manual("/sell 100 AAPL 191", message_id=1, now=yesterday, owner_shares=100)
    lost = queue_manual("/buy 10 TSLA stop 185", message_id=2, now=yesterday)
    today = queue_manual("/buy 5 NVDA 185 stop 180", message_id=3)
    for moment in (yesterday, yesterday, MANUAL_T0):
        assert asyncio.run(mo.claim_next_order(moment + timedelta(seconds=1))) is not None
    asyncio.run(mo.update_order_status(partial, "sent", now=yesterday, ibkr_order_id=55))
    asyncio.run(mo.mark_unknown_sending_rows({f"manual:{partial}", f"manual:{today}"}, now=yesterday))  # TSLA lost
    asyncio.run(mo.update_order_status(today, "sent", now=MANUAL_T0, ibkr_order_id=60))
    # Yesterday's DAY sell filled 40 and then ended at the close; today's buy still works.
    early = execution("0000e0d5.000000aa.01.01", 55, 40, 191.0, acctNumber=ACCOUNT, orderRef=f"manual:{partial}",
                      time="20261012  15:00:00")
    reported = pb.order("BUY", "LMT", 5, ACCOUNT, f"manual:{today}", lmtPrice=185.0, tif="DAY")
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), executions=(early,),
                              live_orders={60: "Submitted"}, order_objects={60: reported}, positions={AAPL_ID: 60})
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=30)))
    bridge.startup()

    assert (manual_row(partial).status, manual_row(partial).filled_qty) == ("cancelled", 40)
    assert manual_row(lost).status == "cancelled" and "earlier session" in manual_row(lost).error
    assert manual_row(today).status == "sent"  # today's row is untouched and still followed
    assert bridge.manual.working_symbols() == {"NVDA"}  # neither old row blocks bot entries any more
    facts = asyncio.run(mo.ledger_facts(symbol="AAPL", now=MANUAL_T0))
    assert facts.open_manual_sell_qty == 0  # nor reserves the owner's AAPL


def test_l3_the_next_order_id_clears_every_id_in_the_state_and_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading.execution import manual_orders as mo

    ledger = manual_ledger(tmp_path, monkeypatch)
    old = queue_manual("/buy 1 TSLA stop 185", message_id=1)
    assert asyncio.run(mo.claim_next_order(MANUAL_T0 + timedelta(seconds=1))) is not None
    asyncio.run(mo.update_order_status(old, "sent", now=MANUAL_T0, ibkr_order_id=150))
    done = bracket("done", "DONE", status="closed", parent=120, filled=100, exit_filled=100)  # legs 120-122
    broker = connected_broker(bid=Decimal("50.10"))  # TWS's sequence restarted at 100
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"done": done})
    bridge.decisions = lambda: [decision_row("d1", "AAPL")]
    bridge.startup()
    bridge.tick()

    # Rewritten for v7c (explicit, not weakened): a buy row holds two ids, its own (150) and its attached
    # stop's (151), so the bot's bracket starts at 152; still never 100-102 again.
    assert [order_id for order_id, _ in broker.placed] == [152, 153, 154]  # never 100-102 again


def test_l3_a_stored_order_id_ibkr_reports_as_another_order_is_never_acted_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.execution import manual_orders as mo

    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL 185 stop 180", message_id=1)
    assert asyncio.run(mo.claim_next_order(MANUAL_T0 + timedelta(seconds=1))) is not None
    asyncio.run(mo.update_order_status(row_id, "sent", now=MANUAL_T0, ibkr_order_id=31))
    # After a reset sequence, id 31 now names a bot leg at IBKR.
    leg = _bot_leg(31, "LMT", parent=30, ref="autotrader:held")
    broker = connected_broker(live_orders={31: "Submitted"}, order_objects={31: leg})
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=30)))
    bridge.startup()
    asyncio.run(mo.request_cancel(row_id[:6], now=MANUAL_T0 + timedelta(seconds=31)))
    bridge.tick()
    bridge.tick()

    assert broker.cancelled == []  # never cancels the bot's order 31
    assert manual_row(row_id).status == "sent"
    assert (f"manual order #{row_id[:6]} (BUY 10 AAPL LMT 185) has no IBKR order id to cancel — check TWS"
            in critical(events(capsys)))


def test_l4_an_owner_close_that_cannot_reprice_is_rejected_once_not_retried_every_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    entering = bracket("bot", "AAPL", status="sent", parent=30)  # the entry still works at the request
    entering.con_id = AAPL_ID
    clock = Clock(ny(11, 0, 2))
    row_id = queue_manual("/close AAPL", message_id=1, now=ny(11, 0), bot_symbols=("AAPL",))
    broker = connected_broker(bid=None, live_orders={30: "Submitted", 31: "PreSubmitted", 32: "PreSubmitted"})
    broker.reqOpenOrders()
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"bot": entering}, clock=clock)
    bridge.tick()
    assert broker.cancelled == [30] and manual_row(row_id).status == "sent"

    broker.execDetails(-1, None, execution("e1", 30, 100, 50.0))  # the entry filled before the cancel
    quotes: list[int] = []
    original = broker.reqMktData
    broker.reqMktData = lambda request_id, contract, *args: (quotes.append(request_id),  # type: ignore[method-assign]
                                                              original(request_id, contract, *args))
    for _ in range(4):
        clock.advance(2)
        bridge.tick()

    assert (manual_row(row_id).status, manual_row(row_id).reject_code) == ("rejected", "flatten_failed")
    assert len(quotes) == 1  # one attempt, not a 4 s quote wait and a CRITICAL every loop


def test_l3_a_cancel_checks_the_order_ref_right_before_cancelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.execution import manual_orders as mo

    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL 185 stop 180", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    # Rewritten for v7c (explicit, not weakened): the buy's attached stop is the second order (D5);
    # a /cancel never cancels it, so nothing at all may be cancelled here.
    [(order_id, _), (stop_id, _)] = broker.placed
    assert stop_id == order_id + 1
    # IBKR now reports that id as another order (a reset sequence): the cancel must not touch it.
    other = pb.order("SELL", "LMT", 100, ACCOUNT, "autotrader:held", lmtPrice=200.0, tif="GTC")
    other.clientId = CLIENT_ID
    broker.openOrder(order_id, SimpleNamespace(symbol="HELD", conId=300), other, SimpleNamespace(status="Submitted"))
    asyncio.run(mo.request_cancel(row_id[:6], now=clock.value))
    capsys.readouterr()
    bridge.tick()

    assert broker.cancelled == []
    assert (f"manual order #{row_id[:6]}: order {order_id} is not reported as manual:{row_id} — not cancelled;"
            " check TWS") in critical(events(capsys))


def test_f1_a_bot_leg_left_by_another_client_id_is_seen_in_the_all_clients_book(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stop the bridge placed when it ran under another client id: reqOpenOrders (client 18) never
    # reports it, so only reqAllOpenOrders shows it, by its autotrader: orderRef.
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/sell 100 AAPL", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100})
    stop = _bot_leg(41, "STP", parent=40, ref="autotrader:otherclient1")
    stop.clientId = 17
    broker.book.append((41, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), stop, "PreSubmitted", 100.0))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.startup()
    bridge.tick()

    assert manual_sells(broker, row_id) == []
    assert manual_row(row_id).reject_code == "bot_holds_symbol"


def test_l5_no_owner_row_is_claimed_in_the_last_seconds_before_the_flatten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1, now=ny(15, 44, 40))
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              positions={300: 100})
    broker.reqOpenOrders()
    clock = Clock(ny(15, 44, 45))
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"held": held}, clock=clock)
    bridge.tick()
    assert broker.placed == [] and manual_row(row_id).status == "queued"  # waits for the flatten

    clock.value = ny(15, 45, 0)
    bridge.tick()
    # Rewritten for v7c (explicit, not weakened): the buy is its entry plus its attached stop, one orderRef (D5).
    assert [sent.orderRef for _, sent in broker.placed] == [
        "autotrader:MOMO_HOD_V2:held", f"manual:{row_id}", f"manual:{row_id}"
    ]  # flatten first


def test_f1_live_bot_legs_block_owner_orders_by_symbol_even_without_a_contract_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    broker = _broker_with_live_bot_legs(41, 42, parent=40, ref="autotrader:yesterday0001")
    broker.open_order_contracts = {41: SimpleNamespace(symbol="AAPL"), 42: SimpleNamespace(symbol="AAPL")}
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.startup()
    bridge.tick()

    assert manual_sells(broker, row_id) == []
    assert manual_row(row_id).reject_code == "bot_holds_symbol"


# ---- re-review 2026-10-13: R1-R3 and L-a..L-e -------------------------------------------------------------


def push_position(broker: FakeBroker, con_id: int, shares: int) -> None:
    """A reqPositions update as TWS pushes it after a fill."""
    broker.position(ACCOUNT, STOCK[con_id], float(shares), 50.0)
    broker.account_positions[con_id] = shares


def _sell_then_closeall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, held: int, sold: int,
                        position_shows_it: bool) -> tuple[Any, FakeBroker, Clock, str]:
    ledger = manual_ledger(tmp_path, monkeypatch)
    first = queue_manual(f"/sell {sold} AAPL", message_id=1, owner_shares=held)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: held}, contracts=STOCK)
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(first_id, _)] = broker.placed
    fill(broker, first_id, sold, 189.99, f"manual:{first}", "0000e0d5.0000b001.01.01")
    if position_shows_it:
        push_position(broker, AAPL_ID, held - sold)  # TWS shows the sale within the same second
    clock.advance(2)
    bridge.tick()  # the bridge sees the fill only now, after the position may already show it
    assert manual_row(first).status == "filled"
    closeall = queue_manual("/closeall", message_id=2, now=clock.value)
    clock.advance(2)
    bridge.tick()
    return bridge, broker, clock, closeall


@pytest.mark.parametrize(("held", "sold"), [(100, 50), (150, 50)])
def test_r1_closeall_sells_what_is_left_once_the_position_shows_the_earlier_sale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: int, sold: int
) -> None:
    bridge, broker, clock, closeall = _sell_then_closeall(tmp_path, monkeypatch, held=held, sold=sold,
                                                          position_shows_it=True)
    # v6 noted the fill after the update had arrived and subtracted the sale twice: 0 or too few sold.
    [(child, sell)] = [(oid, sent) for oid, sent in broker.placed if sent.orderRef == f"manual:{closeall}"]
    assert sell.totalQuantity == held - sold
    fill(broker, child, held - sold, 189.99, f"manual:{closeall}", "0000e0d5.0000b002.01.01")
    push_position(broker, AAPL_ID, 0)
    clock.advance(2)
    bridge.tick()
    assert (manual_row(closeall).status, manual_row(closeall).error) == ("filled", None)


def test_r1_a_second_sell_of_the_shares_still_held_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    first = queue_manual("/sell 50 AAPL", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(first_id, _)] = broker.placed
    fill(broker, first_id, 50, 189.99, f"manual:{first}", "0000e0d5.0000b003.01.01")
    push_position(broker, AAPL_ID, 50)
    clock.advance(2)
    bridge.tick()
    second = queue_manual("/sell 50 AAPL", message_id=2, now=clock.value, owner_shares=50)
    clock.advance(2)
    bridge.tick()

    assert manual_row(second).status == "sent"  # v6 refused it for 15 s: 50 held, none reserved


def test_r1_closeall_never_sells_shares_a_lagging_position_still_shows_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, broker, clock, closeall = _sell_then_closeall(tmp_path, monkeypatch, held=150, sold=50,
                                                          position_shows_it=False)
    [(child, sell)] = [(oid, sent) for oid, sent in broker.placed if sent.orderRef == f"manual:{closeall}"]
    assert sell.totalQuantity == 100  # 150 shown, 50 of them already sold
    fill(broker, child, 100, 189.99, f"manual:{closeall}", "0000e0d5.0000b004.01.01")
    push_position(broker, AAPL_ID, 0)
    clock.advance(2)
    bridge.tick()

    row = manual_row(closeall)  # never "filled" after keeping something back: the owner should look
    assert (row.status, row.error) == (
        "cancelled", "AAPL: 50 sh just sold do not show in the position yet — 100 of 150 sold"
    )


def test_r2_closeall_right_after_a_bot_exit_never_sells_the_bots_shares_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    held = bracket("bot", "AAPL", status="open", parent=30, filled=100)
    held.con_id = AAPL_ID
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK,
                              live_orders={31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("e1", 30, 100, 50.0),))
    broker.reqOpenOrders()
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"bot": held}, clock=clock)
    bridge.tick()
    # The bot's target fills and OCA cancels the stop; TWS's position update has not arrived yet.
    fill(broker, 31, 100, 51.0, "autotrader:bot", "0000e0d5.0000b005.01.01")
    broker.orderStatus(32, "Cancelled", 0, 100, 0)
    closeall = queue_manual("/closeall", message_id=1, now=clock.value)
    clock.advance(2)
    bridge.tick()

    assert held.status == "closed"
    assert manual_sells(broker, closeall) == []  # v6 sold the bot's 100 again: short 100
    row = manual_row(closeall)
    assert (row.status, row.error) == ("rejected", "AAPL: 100 sh just sold do not show in the position yet — not sold")


class PerContractQuotes(FakeBroker):
    """Each contract has its own book."""

    def __init__(self, *args: Any, books: dict[int, tuple[float, float]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.books = books

    def reqMktData(self, request_id: int, contract: Any, *args: Any) -> None:
        bid, ask = self.books[contract.conId]
        self.tickPrice(request_id, 1, bid, None)
        self.tickPrice(request_id, 2, ask, None)


def per_contract_broker(**kwargs: Any) -> PerContractQuotes:
    broker = PerContractQuotes(**kwargs)
    broker.connect("127.0.0.1", 7497, CLIENT_ID)
    broker.reqAccountSummary(1, "All", "NetLiquidation")
    broker.reqPositions()
    return broker


def test_r3_a_partly_refused_closeall_ends_cancelled_and_the_phone_shows_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading.operations.telegram_bot import status_change_text
    from trading.operations.telegram_tools import order_view

    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/closeall", message_id=1)
    broker = per_contract_broker(positions={TSLA_ID: 50, AAPL_ID: 20}, contracts=STOCK,
                                 books={TSLA_ID: (250.00, 250.10), AAPL_ID: (186.00, 190.00)})  # AAPL 2.1% wide
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(child, sell)] = broker.placed
    fill(broker, child, 50, 250.0, f"manual:{row_id}", "0000e0d5.0000b006.01.01")
    clock.advance(2)
    bridge.tick()

    row = manual_row(row_id)  # v6: "✅ Filled CLOSE ALL" with 20 AAPL still held
    assert (row.status, row.error) == ("cancelled", "AAPL: spread 2.13% over 1% — not sold")
    assert status_change_text(order_view(row)) == (
        f"↩️ Cancelled #{row_id[:6]} CLOSE ALL (50 filled before) — AAPL: spread 2.13% over 1% — not sold"
    )


def test_r3_a_closeall_sell_that_ends_unfilled_at_ibkr_ends_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/closeall", message_id=1)
    broker = connected_broker(bid=Decimal("249.99"), ask=Decimal("250.01"), positions={TSLA_ID: 50}, contracts=STOCK)
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(child, _)] = broker.placed
    broker.orderStatus(child, "Cancelled", 0, 50, 0)  # IBKR cancelled the market sell unfilled
    clock.advance(2)
    bridge.tick()

    row = manual_row(row_id)
    assert (row.status, row.error) == ("cancelled", f"order {child}: 0 of 50 sold")


def test_la_a_rejection_tws_never_follows_with_a_status_is_settled_by_asking_tws(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/sell 100 AAPL", message_id=1, owner_shares=100)
    broker = slow_status_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100})
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(order_id, _)] = broker.placed
    broker.error(order_id, 201, "Order rejected - reason: some reason")  # no openOrder or orderStatus follows
    for _ in range(4):  # 8 s: still possibly working
        clock.advance(2)
        bridge.tick()
    assert manual_row(row_id).status == "sent"
    for _ in range(2):  # past 10 s: IBKR has neither an open nor a completed order for it
        clock.advance(2)
        bridge.tick()

    row = manual_row(row_id)  # v6 kept it "sent" all day, blocking the owner's exits and the bot
    assert (row.status, row.reject_code) == ("rejected", "tws_201")
    assert "AAPL" not in bridge.manual.working_symbols()
    assert len(broker.placed) == 1  # never resent


def test_la_an_order_tws_still_works_after_an_error_keeps_being_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/sell 100 AAPL 191", message_id=1, owner_shares=100)
    broker = slow_status_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100})
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(order_id, sent)] = broker.placed
    broker.error(order_id, 10349, "Order TIF was set to DAY based on order preset.")
    working = copy.copy(sent)
    working.clientId, working.permId = CLIENT_ID, 770002
    broker.book.append((order_id, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), working, "Submitted", 100.0))
    for _ in range(7):
        clock.advance(2)
        bridge.tick()

    assert manual_row(row_id).status == "sent" and row_id in bridge.manual.tracked


def _yesterdays_sent_sell(order_id: int) -> str:
    from trading.execution import manual_orders as mo

    yesterday = ny(15, 50) - timedelta(days=1)
    row_id = queue_manual("/sell 100 AAPL 191", message_id=1, now=yesterday, owner_shares=100)
    assert asyncio.run(mo.claim_next_order(yesterday + timedelta(seconds=1))) is not None
    assert asyncio.run(mo.update_order_status(row_id, "sent", now=yesterday + timedelta(seconds=2),
                                              ibkr_order_id=order_id))
    return row_id


@pytest.mark.parametrize("evidence", ["completed", "none"])
def test_lb_an_earlier_session_order_is_settled_from_the_fills_ibkr_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence: str
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = _yesterdays_sent_sell(77)  # yesterday's LMT sell, sent at 15:50
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    if evidence == "completed":  # it filled at 15:58, after the bridge's 15:55 stop
        done = pb.order("SELL", "LMT", 100, ACCOUNT, f"manual:{row_id}", lmtPrice=191.0, tif="DAY")
        done.filledQuantity = 100.0
        broker.completed.append((SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), done, SimpleNamespace(status="Filled")))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(ny(9, 25, 30)))
    bridge.startup()

    row = manual_row(row_id)  # v6 said "cancelled, 0 filled" either way
    if evidence == "completed":
        assert (row.status, row.filled_qty) == ("filled", 100)
    else:
        assert (row.status, row.filled_qty) == ("cancelled", 0)
        assert row.error.endswith("fill unknown — check TWS")


def test_l2_an_earlier_session_row_still_working_at_ibkr_is_followed_not_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = _yesterdays_sent_sell(77)
    reported = pb.order("SELL", "LMT", 100, ACCOUNT, f"manual:{row_id}", lmtPrice=191.0, tif="DAY")
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100},
                              live_orders={77: "Submitted"}, order_objects={77: reported})
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(ny(9, 25, 30)))
    bridge.startup()

    assert manual_row(row_id).status == "sent" and row_id in bridge.manual.tracked


def test_lc_new_order_ids_skip_the_live_orders_of_an_earlier_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    # After an order-id reset (nextValidId 100) the earlier session's live GTC legs keep ids 100 and 101.
    target = _bot_leg(100, "LMT", parent=99, ref="autotrader:yesterday0001")
    stop = _bot_leg(101, "STP", parent=99, ref="autotrader:yesterday0001")
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100},
                              live_orders={100: "Submitted", 101: "PreSubmitted"}, order_objects={100: target, 101: stop})
    # Rewritten for v7c (explicit, not weakened): TWS reports those legs with their AAPL contract, as it
    # does in production. v7c reads that contract: the live GTC stop leg is what covers the 100 AAPL, and
    # without it every buy is refused (v7c_unprotected_position) and this test would place nothing.
    contract = SimpleNamespace(conId=AAPL_ID, symbol="AAPL")
    broker.open_order_contracts = {100: contract, 101: contract}
    queue_manual("/buy 5 TSLA stop 185", message_id=1)
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.startup()
    bridge.tick()

    # v7c: the buy and its attached stop take the next two free ids (D5).
    [(order_id, sent), (stop_id, attached)] = broker.placed
    assert order_id == 102 and sent.action == "BUY"  # v6 reused 100, the id of a live GTC leg
    assert (stop_id, attached.action, attached.orderType) == (103, "SELL", "STP")


def test_le_a_position_that_never_shows_a_fill_keeps_the_shares_reserved_and_alerts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    first = queue_manual("/sell 100 AAPL", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(first_id, _)] = broker.placed
    fill(broker, first_id, 100, 189.99, f"manual:{first}", "0000e0d5.0000b007.01.01")  # no position update ever
    capsys.readouterr()
    for _ in range(10):  # 20 s
        clock.advance(2)
        bridge.tick()
    second = queue_manual("/sell 100 AAPL", message_id=2, now=clock.value, owner_shares=100)
    clock.advance(1)
    bridge.tick()

    assert manual_row(second).reject_code == "insufficient_shares"  # v6 released the 100 after 15 s: short 100
    alerts = [text for text in critical(events(capsys)) if "no position update" in text]
    assert len(alerts) == 1 and alerts[0].startswith("AAPL: no position update 1")
    assert alerts[0].endswith(f"after a fill of 100 sh (order {first_id}) — those shares stay reserved; check TWS")


# ---- v7a: per-identity authority (exact names, caps, fences, orderRef, preflight) ------------------


def _fence_row(db_path: Path, identity: str, stopped: int) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            "INSERT INTO autotrader_execution_fences (name, stopped, reason, updated_at) VALUES (?, ?, 'test', 't')"
            " ON CONFLICT(name) DO UPDATE SET stopped=excluded.stopped",
            (f"paper:{identity}", stopped),
        )
        connection.commit()


V7C_BUDGET_LINE = ("day risk budget 3% of net liquidation, account level (realized loss + open risk to current stops"
                   " + new risk; stop slippage factor 1.0)")
V7B_CAPS_LINES = [
    "sendable identities = GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2",
    "caps GAP_GO_V2 positions=1 entries/day=1 last_entry=close-30m",
    "caps MOMO_HOD_V2 positions=3 entries/day=none last_entry=close-60m",
    "caps ORB_V2 positions=1 entries/day=1 last_entry=close-30m",
    "caps VWAP_PB_V2 positions=1 entries/day=2 last_entry=close-30m",
]


def _bracket_named(name: str, decision_id: str = "d") -> Any:
    return pb.Bracket(decision_id=decision_id, symbol="SYM", con_id=1, primary_exchange="NASDAQ",
                      strategy=pb.strategy_family(name), strategy_name=name, qty=0, entry="0", stop="0", target="0")


@pytest.mark.parametrize("name", ["MOMO_HOD_V2", " MOMO_HOD_V2 ", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2"])
def test_v7a_exactly_the_sendable_names_send(tmp_path: Path, name: str) -> None:
    assert pb.may_send_orders(name)
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL", strategy=name),))
    bridge.tick()
    assert [order_id for order_id, _ in broker.placed] == [100, 101, 102]


@pytest.mark.parametrize(
    "name", ["MOMO_HOD_V1", "AP_MOMO_HOD_V1", "momo_hod_v2", "MOMO_HOD_V2X", "ORB_V1", "orb_v2", "ORB_V2X",
             "VWAP_PB_V1", "GAP_GO_V1", "SH_MOMO_HOD_V3", ""]
)
def test_v7a_a_name_that_is_not_exactly_sendable_is_shadow_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    assert not pb.may_send_orders(name)  # v5 matched the MOMO_HOD prefix: AP_MOMO_HOD_V1 and MOMO_HOD_V2X sent
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL", strategy=name),))
    capsys.readouterr()
    bridge.tick()
    assert broker.placed == []
    assert bridge.brackets["d1"].skip_reason == pb.SHADOW_ONLY


def test_v7a_every_sendable_name_has_a_caps_row_and_nothing_else_does(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pb.SENDABLE_STRATEGY_NAMES == frozenset(pb.IDENTITY_CAPS) == {
        "MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2"
    }
    # The owner's caps table (plan rev 3): MOMO as before; one position each for the three.
    assert pb.IDENTITY_CAPS["MOMO_HOD_V2"] == pb.Caps(3, None, timedelta(minutes=60))
    assert pb.IDENTITY_CAPS["ORB_V2"] == pb.Caps(1, 1, timedelta(minutes=30))
    assert pb.IDENTITY_CAPS["VWAP_PB_V2"] == pb.Caps(1, 2, timedelta(minutes=30))
    assert pb.IDENTITY_CAPS["GAP_GO_V2"] == pb.Caps(1, 1, timedelta(minutes=30))
    assert pb.global_max_open() == 6 == sum(caps.max_positions for caps in pb.IDENTITY_CAPS.values())
    assert pb.caps_problem() is None
    assert pb.caps_lines() == V7B_CAPS_LINES
    shipped = dict(pb.IDENTITY_CAPS)
    monkeypatch.setattr(pb, "IDENTITY_CAPS", {})
    assert pb.caps_problem() == "PREFLIGHT_REFUSED caps: GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2"
    assert pb.global_max_open() == 0
    monkeypatch.setattr(pb, "IDENTITY_CAPS", {**shipped, "SOMEDAY_V2": pb.Caps(1, 1, timedelta(minutes=30))})
    assert pb.caps_problem() == "PREFLIGHT_REFUSED caps: SOMEDAY_V2"


@pytest.mark.anyio
async def test_v7a_an_identity_fence_blocks_only_that_identitys_new_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path, ids = await seed_decisions(tmp_path, monkeypatch)
    assert pb.identity_fence_active(db_path, "MOMO_HOD_V2") is False and pb.fenced_identities(db_path) == []
    _fence_row(db_path, "MOMO_HOD_V2", 1)
    assert pb.identity_fence_active(db_path, "MOMO_HOD_V2") is True
    assert pb.fenced_identities(db_path) == ["MOMO_HOD_V2"]
    assert pb.paper_kill_switch_active(db_path) is False  # the global switch is a different row

    held = bracket("held", "HELD", status="open", parent=30, filled=100)
    broker = connected_broker(bid=Decimal("50.10"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              positions={300: 100})
    bridge = make_bridge(tmp_path, broker, db=db_path, brackets={"held": held})
    bridge.startup()
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == [] and broker.cancelled == []
    out = events(capsys)
    assert ("SKIP", "AAPL (MOMO_HOD_V2): identity_stopped:MOMO_HOD_V2") in out
    assert bridge.brackets[ids["momo"]].skip_reason == "identity_stopped:MOMO_HOD_V2"
    assert critical(out) == [] and held.status == "open"  # exits of the held bracket are still managed

    _fence_row(db_path, "MOMO_HOD_V2", 0)  # autotraderctl paper resume --identity MOMO_HOD_V2
    restarted = make_bridge(tmp_path, connected_broker(bid=Decimal("50.10")), db=db_path)
    restarted.tick()
    assert restarted.brackets[ids["momo"]].status == "sent"


def test_v7a_an_unreadable_identity_fence_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(sqlite3.OperationalError):
        pb.fenced_identities(empty)

    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL"),))
    bridge.identity_fence_active = lambda identity: pb.identity_fence_active(empty, identity)
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []
    assert ("SKIP", "AAPL: identity fence unreadable (OperationalError) — fail closed") in events(capsys)


def test_v7a_the_connect_line_names_the_sendable_set_its_caps_and_the_fences(
    bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    broker = FakeBroker()
    broker.disconnect_after = 2
    bridge_env.install(broker, Clock(ny(10, 0)))
    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_RESTART
    out = events(capsys)
    # Rewritten for v7c (explicit, not weakened): the version is v7c and the day risk budget line follows
    # the fence line (the dry run's `day risk budget 3%` check reads the same line from --preflight).
    start = out.index(next(item for item in out if item[1].startswith("PAPER bridge v7c connected")))
    assert out[start + 1:start + 8] == [
        *(("INFO", line) for line in V7B_CAPS_LINES),
        ("INFO", "fenced identities = none"),
        ("INFO", V7C_BUDGET_LINE),
    ]

    _fence_row(pb.DB, "MOMO_HOD_V2", 1)
    broker = FakeBroker()
    broker.disconnect_after = 2
    bridge_env.install(broker, Clock(ny(10, 0)))
    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_RESTART
    assert ("CRITICAL", "fenced identities = MOMO_HOD_V2") in events(capsys)


def test_v7a_the_identity_position_cap_is_checked_before_the_global_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # MOMO may hold one; a second row lifts the global cap to 6, so only the identity cap can block.
    monkeypatch.setattr(pb, "IDENTITY_CAPS", {
        "MOMO_HOD_V2": pb.Caps(1, None, timedelta(minutes=60)),
        "SOMEDAY_V2": pb.Caps(5, None, timedelta(minutes=30)),
    })
    held = bracket("a", "AAA", status="open", parent=40, filled=100)
    broker = connected_broker(bid=Decimal("50.10"), positions={400: 100})
    bridge = make_bridge(tmp_path, broker, brackets={"a": held}, decisions=(decision_row("n", "NEWX"),))
    bridge.tick()
    assert broker.placed == []
    assert bridge.brackets["n"].skip_reason == "MOMO_HOD_V2: max 1 positions already active"
    # The global cap was not the reason: one slot used of six (the sum of both rows).
    assert bridge.slots_used() == 1 and pb.global_max_open() == 6


def test_v7a_entries_per_day_count_every_attempt_including_a_cancelled_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pb, "SENDABLE_STRATEGY_NAMES", frozenset({"MOMO_HOD_V2", "VWAP_PB_V2"}))
    monkeypatch.setattr(pb, "IDENTITY_CAPS", {
        "MOMO_HOD_V2": pb.Caps(3, None, timedelta(minutes=60)),
        "VWAP_PB_V2": pb.Caps(1, 1, timedelta(minutes=30)),
    })
    cancelled = replace(bracket("c", "CANX", status="cancelled", parent=40), strategy="other",
                        strategy_name="VWAP_PB_V2")
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, brackets={"c": cancelled}, decisions=(
        decision_row("v", "NEWV", strategy="VWAP_PB_V2"), decision_row("m", "NEWM"),
    ))
    assert bridge.entries_today("VWAP_PB_V2") == 1 and bridge.positions_of("VWAP_PB_V2") == 0
    capsys.readouterr()
    bridge.tick()

    assert bridge.brackets["v"].skip_reason == "VWAP_PB_V2: max 1 entries/day already sent"
    assert bridge.brackets["m"].status == "sent"
    assert [sent.orderRef for _, sent in broker.placed] == ["autotrader:MOMO_HOD_V2:m"] * 3
    assert bridge.entries_today("MOMO_HOD_V2") == 1


def test_v7a_a_sendable_identitys_last_entry_is_its_caps_offset_from_the_real_close(tmp_path: Path) -> None:
    normal = make_bridge(tmp_path, connected_broker())
    assert normal.entry_deadline(_bracket_named("MOMO_HOD_V2")) == ny(15, 0)  # v5's STRATEGY_CUTOFFS["momo"]
    assert normal.entry_deadline(_bracket_named("ORB_V1")) == ny(12, 0)  # not sendable: family cutoff
    assert normal.entry_deadline(_bracket_named("VWAP_PB_V1")) == ny(15, 30)  # no family cutoff: last_entry

    half_day = date(2026, 11, 27)  # 13:00 close
    half = make_bridge(tmp_path, connected_broker(), session_date=half_day)
    assert half.entry_deadline(_bracket_named("MOMO_HOD_V2")) == datetime(2026, 11, 27, 12, 0, tzinfo=pb.NY)
    assert half.entry_deadline(_bracket_named("ORB_V1")) == datetime(2026, 11, 27, 12, 0, tzinfo=pb.NY)
    assert half.entry_deadline(_bracket_named("VWAP_PB_V1")) == datetime(2026, 11, 27, 12, 30, tzinfo=pb.NY)

    # The gate and the working-parent cancel use the same deadline.
    late = Clock(ny(15, 0))
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, clock=late, decisions=(decision_row("d1", "AAPL", created=ny(14, 59, 50)),))
    bridge.tick()
    assert broker.placed == [] and bridge.brackets["d1"].skip_reason == "momo entry window closed"
    working = bracket("w", "WORK", status="sent", parent=40)
    bridge = make_bridge(tmp_path, broker, clock=late, brackets={"w": working})
    bridge.expire_entries()
    assert working.parent_cancel_requested is True and broker.cancelled == [40]


def test_v7a_a_sendable_name_without_a_caps_row_is_skipped_and_critical_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pb, "IDENTITY_CAPS", {})
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL"), decision_row("d2", "MSFT")))
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []
    assert {b.skip_reason for b in bridge.brackets.values()} == {"identity_caps_missing:MOMO_HOD_V2"}
    assert critical(events(capsys)) == [
        "MOMO_HOD_V2: sendable but has no caps row — its entries are skipped (fail closed)"
    ]


def test_v7a_the_order_ref_carries_the_identity_and_parses_back(tmp_path: Path) -> None:
    bridge = make_bridge(tmp_path, connected_broker())
    for name in pb.SENDABLE_STRATEGY_NAMES:
        ref = bridge.ref(_bracket_named(name, decision_id="abcdefghijklmnop"))
        assert ref == f"autotrader:{name}:abcdefghijkl" and ref.startswith(pb.ORDER_REF_PREFIX)
        assert pb.parse_ref(ref) == (name, "abcdefghijkl")
    assert pb.parse_ref("manual:42") is None
    assert pb.parse_ref("autotrader:held") is None  # a v5 ref has no identity
    assert pb.parse_ref("autotrader::x") is None and pb.parse_ref(None) is None
    # The prefix matchers still accept every bridge ref, old and new.
    row = SimpleNamespace(account=ACCOUNT, day=SESSION, order_ref="autotrader:MOMO_HOD_V2:abcdefghijkl")
    assert pb.execution_matches(row, ACCOUNT, SESSION, pb.ORDER_REF_PREFIX)


def test_v7a_preflight_prints_the_identity_lines_without_connecting(
    bridge_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge_env.install(None, Clock(ny(10, 0)))  # the App factory fails the test on any connect

    assert pb.main(["--preflight"]) == pb.EXIT_OK
    out = events(capsys)
    # Rewritten for v7c (explicit, not weakened): --preflight also prints the day risk budget line, exactly once.
    assert out[1:] == [
        *(("INFO", line) for line in V7B_CAPS_LINES),
        ("INFO", "fenced identities = none"),
        ("INFO", V7C_BUDGET_LINE),
    ]
    assert pb.main(["--preflight", "--date", "2026-10-13"]) == pb.EXIT_OK
    assert not bridge_env.state_file.exists()

    _fence_row(pb.DB, "MOMO_HOD_V2", 1)
    assert pb.main(["--preflight"]) == pb.EXIT_OK
    assert ("CRITICAL", "fenced identities = MOMO_HOD_V2") in events(capsys)

    monkeypatch.setattr(pb, "IDENTITY_CAPS", {})
    assert pb.main(["--preflight"]) == pb.EXIT_REFUSED
    assert ("CRITICAL", "PREFLIGHT_REFUSED caps: GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2") in events(capsys)
    monkeypatch.undo()


def test_v7a_preflight_refuses_an_unreadable_fence_or_config(
    bridge_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge_env.install(None, Clock(ny(10, 0)))
    empty = pb.DB.parent / "empty.db"
    sqlite3.connect(empty).close()
    monkeypatch.setattr(pb, "DB", empty)
    assert pb.main(["--preflight"]) == pb.EXIT_REFUSED
    assert any(text.startswith("PREFLIGHT_REFUSED fences unreadable (OperationalError") for text in critical(events(capsys)))

    monkeypatch.setattr(pb, "CONFIG", pb.DB.parent / "missing.toml")
    assert pb.main(["--preflight"]) == pb.EXIT_REFUSED
    assert any(text.startswith("refusing: config") for text in critical(events(capsys)))


def test_v7a_the_bridge_never_reconnects_in_process() -> None:
    """A lost connection is exit 75 (test_tws_disconnect_exits_75_for_a_supervised_restart) and the
    restart re-runs _account_refusal: no code path connects a second time inside one process."""
    import ast

    tree = ast.parse(BRIDGE_PATH.read_text(encoding="utf-8"))
    connect_callers: set[str] = set()
    private_connect_callers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            if (isinstance(call.func, ast.Attribute) and call.func.attr == "connect"
                    and not (isinstance(call.func.value, ast.Name) and call.func.value.id == "sqlite3")):
                connect_callers.add(node.name)  # TWS connects only; sqlite3.connect is the ledger
            if isinstance(call.func, ast.Name) and call.func.id == "_connect":
                private_connect_callers.add(node.name)
    assert connect_callers == {"_connect"}
    assert private_connect_callers == {"_connected_session"}


@pytest.mark.parametrize("row", [
    dict(max_positions=0, max_entries_per_day=None, last_entry_before_close=timedelta(minutes=60)),
    dict(max_positions=True, max_entries_per_day=None, last_entry_before_close=timedelta(minutes=60)),
    dict(max_positions=1, max_entries_per_day=0, last_entry_before_close=timedelta(minutes=60)),
    dict(max_positions=1, max_entries_per_day=None, last_entry_before_close=timedelta(0)),
    dict(max_positions=1, max_entries_per_day=None, last_entry_before_close=timedelta(hours=7)),
    dict(max_positions=1, max_entries_per_day=None, last_entry_before_close=60),
])
def test_v7a1_a_caps_row_that_could_widen_anything_is_refused(row: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        pb.Caps(**row)


def test_v7a1_the_caps_table_is_read_only_and_valid() -> None:
    with pytest.raises(TypeError):
        pb.IDENTITY_CAPS["ORB_V2"] = pb.Caps(1, 1, timedelta(minutes=30))  # type: ignore[index]
    with pytest.raises(TypeError):
        del pb.IDENTITY_CAPS["MOMO_HOD_V2"]  # type: ignore[attr-defined]
    for caps in pb.IDENTITY_CAPS.values():
        pb.Caps(caps.max_positions, caps.max_entries_per_day, caps.last_entry_before_close)
    assert pb.caps_problem() is None


def test_v7a1_the_shared_read_only_opener_retries_a_transient_open_and_fails_closed_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "ledger.db"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE autotrader_execution_fences (name TEXT PRIMARY KEY, stopped INTEGER)")
        connection.commit()
    real_connect = pb.sqlite3.connect
    failures = {"left": 2}
    calls: list[str] = []

    def flaky(path: str, **kwargs: Any) -> Any:
        calls.append(path)
        if failures["left"]:
            failures["left"] -= 1
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(path, **kwargs)

    monkeypatch.setattr(pb.sqlite3, "connect", flaky)
    monkeypatch.setattr(pb.time, "sleep", lambda _s: None)
    assert pb.paper_kill_switch_active(db) is False  # two transient failures, then the real open
    assert len(calls) == 3 and all(uri.endswith("?mode=ro") for uri in calls)

    failures["left"] = 10  # never recovers: the last attempt raises, callers stay fail-closed
    with pytest.raises(sqlite3.OperationalError):
        pb.paper_kill_switch_active(db)

    calls.clear()
    with pytest.raises(sqlite3.OperationalError):
        pb.paper_kill_switch_active(tmp_path / "missing.db")  # no file: no retry at all
    assert len(calls) == 1


# ---- v7c: the account-level 3 % day risk budget (owner decisions D1, D5; plan P3-B1/B2) ----------
#
# The fake broker's net liquidation is 665,000.00: the budget is 19,950.00 (3 %) and the daily loss
# limit 13,300.00 (2 %). A default decision buys 100 @ 50.00 with its stop at 49.50: new risk 50.00.

# Changed by v7c fix 1 (N18 LOW-5): "or in TWS", since /close is refused on a past session's bot leg.
V7C_ADVICE_FOR_AAPL = "close it with /close AAPL or in TWS, or protect it with a stop that stays working (GTC)"


def stop_leg(order_id: int, quantity: int, price: float, *, parent: int, ref: str = "autotrader:MOMO_HOD_V2:open",
             client: int = CLIENT_ID) -> Any:
    """A SELL STP as TWS reports it: a bracket's stop leg, an owner's TWS stop (client 0, no parent)."""
    leg = pb.order("SELL", "STP", quantity, ACCOUNT, ref, auxPrice=price, tif="GTC", parentId=parent)
    leg.clientId, leg.permId = client, 770000 + order_id
    return leg


def v7c_world(tmp_path: Path, *, held: int, shown: float | None = 48.0, stop_status: str = "PreSubmitted",
              positions: dict[int, int] | None = None, clock: Clock | None = None, decisions: tuple[Any, ...] = (),
              **broker_kwargs: Any) -> tuple[Any, FakeBroker, Any]:
    """One open MOMO bracket OPEN (orders 30-32, conId 300) holding ``held`` at 50.00, its stop intent 48.00,
    and the stop leg as IBKR reports it (``shown``; None = no openOrder yet): risk held x (50 - stop)."""
    item = bracket("open", "OPEN", status="open", parent=30, filled=held, qty=held)
    item.stop = "48.00"
    reported = {} if shown is None else {32: stop_leg(32, held, shown, parent=30)}
    broker = connected_broker(bid=Decimal("50.10"), live_orders={31: "Submitted", 32: stop_status},
                              order_objects=reported, positions={300: held, **(positions or {})}, **broker_kwargs)
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets={"open": item}, decisions=decisions, clock=clock)
    return bridge, broker, item


def v7c_skip(bridge: Any, decision_id: str) -> str:
    return bridge.brackets[decision_id].skip_reason


WOULD_HAVE = " — would have sent BUY 100 @ 50.00 | stop 49.50 | target 51.00"


def test_v7c_constants_pinned() -> None:
    # Owner decision D1 (2026-10-18): 3 % account level, factor pinned, the -2 % block unchanged (O14).
    assert pb.BRIDGE_VERSION == "v7c"
    assert pb.DAY_RISK_BUDGET_PCT == Decimal("3") and pb.STOP_SLIPPAGE_FACTOR == Decimal("1.0")
    assert pb.MAX_DAILY_LOSS_PCT == Decimal("2") and pb.MAX_ORDER_RISK_PCT == Decimal("1")
    assert pb.BUDGET_PAGE_STEP_PCT == Decimal("0.5")
    assert (pb.PNL_FRESH, pb.PNL_MISMATCH_MIN_USD, pb.PNL_MISMATCH_NLV_PCT) == (
        timedelta(seconds=120), Decimal("25"), Decimal("0.05"))
    assert (pb.ORDER_BOOK_REFRESH, pb.ORDER_BOOK_MAX_AGE, pb.ORDER_BOOK_TIMEOUT_S) == (
        timedelta(seconds=30), timedelta(seconds=90), 3.0)
    assert (pb.RELEASE_TIMEOUT, pb.STOP_FIT_GRACE) == (timedelta(seconds=10), timedelta(seconds=15))
    assert (pb.V7C_DAY_RISK_BUDGET, pb.V7C_BUDGET_OK, pb.V7C_BUDGET_OVER, pb.V7C_STOP_WIDENED, pb.V7C_STOP_UNCONFIRMED,
            pb.V7C_UNPROTECTED_POSITION, pb.V7C_BUDGET_INVARIANT_BROKEN, pb.V7C_WRONG_THREAD, pb.V7C_PNL_SOURCE,
            pb.V7C_PNL_MISMATCH, pb.DAILY_LOSS_LIMIT) == (
        "v7c_day_risk_budget", "v7c_budget_ok", "v7c_budget_over", "v7c_stop_widened", "v7c_stop_unconfirmed",
        "v7c_unprotected_position", "v7c_budget_invariant_broken", "v7c_wrong_thread", "v7c_pnl_source",
        "v7c_pnl_mismatch", "daily_loss_limit")
    assert (pb.ENTRY_STOP_RELEASE_UNCONFIRMED, pb.MANUAL_BUY_STILL_WORKING) == (
        "entry_stop_release_unconfirmed", "manual_buy_still_working")
    # deploy apply recognises a v7c bridge by this line start (deploy_apply.py `budget=`).
    assert b"\nDAY_RISK_BUDGET_PCT = " in BRIDGE_PATH.read_bytes()


@pytest.mark.parametrize(("constant", "value", "message"), [
    ("STOP_SLIPPAGE_FACTOR", 'Decimal("0.9")', "STOP_SLIPPAGE_FACTOR must be >= 1"),
    ("STOP_SLIPPAGE_FACTOR", 'Decimal("0")', "STOP_SLIPPAGE_FACTOR must be >= 1"),
    ("DAY_RISK_BUDGET_PCT", 'Decimal("30")', "DAY_RISK_BUDGET_PCT must be in (0, 3]"),
    ("DAY_RISK_BUDGET_PCT", 'Decimal("0")', "DAY_RISK_BUDGET_PCT must be in (0, 3]"),
])
def test_v7c_import_refuses_factor_below_one(tmp_path: Path, constant: str, value: str, message: str) -> None:
    source = BRIDGE_PATH.read_text(encoding="utf-8")
    pinned = {"STOP_SLIPPAGE_FACTOR": 'STOP_SLIPPAGE_FACTOR = Decimal("1.0")',
              "DAY_RISK_BUDGET_PCT": 'DAY_RISK_BUDGET_PCT = Decimal("3")'}[constant]
    assert source.count(f"\n{pinned}\n") == 1
    edited = tmp_path / "paper_bridge.py"
    edited.write_text(source.replace(f"\n{pinned}\n", f"\n{constant} = {value}\n"), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("paper_bridge_edited", edited)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        with pytest.raises(ValueError, match=re.escape(message)):
            spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)


def test_v7c_position_risk_is_held_at_avg_and_unfilled_at_limit_never_negative() -> None:
    f = Decimal("1.0")
    assert pb.position_risk(400, Decimal("49.5"), 600, Decimal("50"), Decimal("48"), f) == Decimal("1800")
    assert pb.position_risk(100, Decimal("50"), 0, Decimal("50"), Decimal("50"), f) == 0  # break-even frees it
    assert pb.position_risk(100, Decimal("50"), 0, Decimal("50"), Decimal("51"), f) == 0  # never a credit
    assert pb.position_risk(100, Decimal("50"), 100, Decimal("50"), Decimal("49"), Decimal("1.5")) == Decimal("300")


def test_v7c_within_budget_sent_with_budget_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bridge, broker, _ = v7c_world(tmp_path, held=9900, decisions=(decision_row("d1", "AAPL"),))  # open 19,800
    capsys.readouterr()
    bridge.tick()

    assert [sent.action for _, sent in broker.placed] == ["BUY", "SELL", "SELL"]
    out = events(capsys)
    ok = ("INFO", "v7c_budget_ok AAPL (MOMO_HOD_V2): realized 0.00 + open 19800.00 + new 50.00 = 19850.00 (2.98%)"
          " <= 3% of 665000.00 (source=bot)")
    assert ok in out
    assert out.index(ok) < next(index for index, (kind, _) in enumerate(out) if kind == "SENT")
    assert not critical(out)


def test_v7c_over_budget_skipped_with_token_and_numbers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bridge, broker, _ = v7c_world(tmp_path, held=9965, decisions=(decision_row("d1", "AAPL"),))  # open 19,930
    capsys.readouterr()
    bridge.tick()

    assert broker.placed == []
    reason = ("v7c_day_risk_budget: realized 0.00 + open 19930.00 + new 50.00 = 19980.00 (3.00%) > 3% of 665000.00"
              f" (source=bot){WOULD_HAVE}")
    assert v7c_skip(bridge, "d1") == reason
    assert ("SKIP", f"AAPL (MOMO_HOD_V2): {reason}") in events(capsys)


def test_v7c_realized_gains_never_fund_risk(tmp_path: Path) -> None:
    won = bracket("won", "WON", status="closed", parent=60, filled=100, exit_filled=100, realized_pnl="5000.00")
    bridge, broker, _ = v7c_world(tmp_path, held=9965, decisions=(decision_row("d1", "AAPL"),))
    bridge.brackets["won"] = won
    bridge.tick()

    assert bridge.account_realized_loss() == 0  # a +5,000 day is a 0 loss, never -5,000
    assert broker.placed == [] and v7c_skip(bridge, "d1").startswith("v7c_day_risk_budget: realized 0.00 + open")


def test_v7c_unfilled_entry_counts_limit_minus_stop(tmp_path: Path) -> None:
    sent = bracket("sent", "SENT", status="sent", parent=30, qty=9965)  # nothing filled, the entry works
    sent.stop = "48.00"
    broker = connected_broker(bid=Decimal("50.10"), live_orders={30: "Submitted", 31: "Submitted", 32: "PreSubmitted"},
                              order_objects={32: stop_leg(32, 9965, 48.0, parent=30)})
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets={"sent": sent}, decisions=(decision_row("d1", "AAPL"),))

    assert bridge.account_open_risk().total == Decimal("19930")  # 9,965 x (50.00 - 48.00), nothing held yet
    bridge.tick()
    assert broker.placed == [] and v7c_skip(bridge, "d1").startswith(
        "v7c_day_risk_budget: realized 0.00 + open 19930.00 + new 50.00")
    broker.orderStatus(30, "Cancelled", 0, 9965, 0)  # the dead entry can buy nothing more
    assert bridge.account_open_risk().total == 0


def test_v7c_partial_fill_held_at_avg_pending_at_limit(tmp_path: Path) -> None:
    part = bracket("part", "PART", status="open", parent=30, filled=400, qty=1000)
    part.stop, part.avg_entry = "48.00", "49.5000"
    broker = connected_broker(live_orders={30: "Submitted", 31: "Submitted", 32: "PreSubmitted"},
                              order_objects={32: stop_leg(32, 1000, 48.0, parent=30)}, positions={300: 400})
    broker.reqOpenOrders()
    bridge = make_bridge(tmp_path, broker, brackets={"part": part})

    risk = bridge.account_open_risk()
    assert risk.total == Decimal("1800")  # 400 x (49.50 - 48.00) + 600 x (50.00 - 48.00)
    assert risk.unprotected == ()


def test_v7c_stop_moved_up_in_tws_frees_budget(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, _ = v7c_world(tmp_path, held=9965, clock=clock, decisions=(decision_row("d1", "AAPL"),))
    broker.own_in_all_open_orders = True  # TWS reports this client's orders to reqAllOpenOrders too
    bridge.tick()
    assert broker.placed == [] and v7c_skip(bridge, "d1").startswith("v7c_day_risk_budget")

    # The owner raises the stop to break-even in TWS. No openOrder is pushed (spike V-1): only the
    # fixed-cadence refresh of the open-orders book shows the new price.
    broker.reported_orders[32] = stop_leg(32, 9965, 50.0, parent=30)
    clock.advance(31)
    bridge.decisions = lambda: [decision_row("d2", "MSFT", created=clock.value, con_id=4932)]
    capsys.readouterr()
    bridge.tick()

    assert bridge.current_stop(bridge.brackets["open"]) == Decimal("50.0")
    assert bridge.brackets["d2"].status == "sent"
    out = events(capsys)
    assert ("INFO", "v7c stop OPEN order 32: 48.0 -> 50.0") in out  # every observed change is logged (V-1)
    assert ("INFO", "v7c_budget_ok MSFT (MOMO_HOD_V2): realized 0.00 + open 0.00 + new 50.00 = 50.00 (0.01%)"
            " <= 3% of 665000.00 (source=bot)") in out


def test_v7c_stop_widened_counted_pages_once_never_flattens(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, _ = v7c_world(tmp_path, held=4000, shown=47.0, clock=clock)  # intent 48.00, TWS shows 47.0
    capsys.readouterr()
    for _ in range(3):
        bridge.tick()
        clock.advance(2)

    assert bridge.account_open_risk().total == Decimal("12000")  # 4,000 x (50 - 47), never the intent's 8,000
    assert critical(events(capsys)) == [
        "v7c_stop_widened OPEN intent 48.00 now 47.0 — counted at 47.0 in the day risk budget; never flattened"
    ]
    assert broker.placed == [] and broker.cancelled == []  # paged, never flattened or modified
    broker.openOrder(32, SimpleNamespace(symbol="OPEN", conId=300), stop_leg(32, 4000, 46.0, parent=30),
                     SimpleNamespace(status="PreSubmitted"))
    bridge.tick()
    assert critical(events(capsys)) == [
        "v7c_stop_widened OPEN intent 48.00 now 46.0 — counted at 46.0 in the day risk budget; never flattened"
    ]  # once per bracket and price
    assert broker.placed == [] and broker.cancelled == []


def test_v7c_no_echo_uses_intent_stop_logs_unconfirmed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bridge, broker, item = v7c_world(tmp_path, held=1000, shown=None)  # IBKR never reported the stop's price
    capsys.readouterr()
    bridge.tick()
    bridge.tick()

    assert bridge.current_stop(item) == Decimal("48.00")  # O5: never assumed raised
    assert bridge.account_open_risk().total == Decimal("2000")
    assert [line for kind, line in events(capsys) if "v7c_stop_unconfirmed" in line] == [
        "v7c_stop_unconfirmed OPEN: IBKR has not reported stop order 32 yet — its intent stop 48.00 counts"
    ]  # INFO, once


def test_v7c_dead_stop_blocks_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 TSLA stop 185", message_id=1)
    item = bracket("open", "OPEN", status="open", parent=30, filled=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"),
                              live_orders={31: "Submitted", 32: "Cancelled"}, positions={300: 100})
    broker.reqOpenOrders()
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"open": item}, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.decisions = lambda: [decision_row("d1", "AAPL", created=MANUAL_T0)]
    bridge.tick()

    why = ("v7c_unprotected_position: OPEN 100 sh of the MOMO_HOD_V2 bracket, whose stop order 32 is not live —"
           " every risk-increasing order is refused; close it with /close OPEN or in TWS, or protect it with a stop"
           " that stays working (GTC)")  # "or in TWS" added by v7c fix 1 (N18 LOW-5)
    assert v7c_skip(bridge, "d1") == f"{why} (source=bot){WOULD_HAVE}"  # O3
    row = manual_row(row_id)
    assert (row.status, row.reject_code, row.error) == (
        "rejected", "v7c_unprotected_position", f"{why} (source=manual). Nothing sent.")
    assert broker.placed == []


def test_v7c_unprotected_foreign_position_blocks_bot_and_manual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 TSLA stop 185", message_id=1)
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)  # 100 AAPL held (carried over), no stop anywhere
    bridge.decisions = lambda: [decision_row("d1", "MSFT", created=clock.value, con_id=4932)]
    capsys.readouterr()
    bridge.tick()

    why = ("v7c_unprotected_position: AAPL 100 sh held, working stops cover 0 — every risk-increasing order is refused;"
           f" {V7C_ADVICE_FOR_AAPL}")
    assert v7c_skip(bridge, "d1") == f"{why} (source=bot){WOULD_HAVE}"
    assert (manual_row(row_id).reject_code, manual_row(row_id).error) == (
        "v7c_unprotected_position", f"{why} (source=manual). Nothing sent.")
    assert broker.placed == []
    assert not critical(events(capsys))  # a position update may still be on its way: the page waits 15 s

    page = ("v7c_unprotected_position AAPL: 100 sh held, working stops cover 0 — every risk-increasing order (bot and"
            f" manual) is refused; {V7C_ADVICE_FOR_AAPL}")
    for _ in range(10):
        clock.advance(2)
        bridge.tick()
    assert critical(events(capsys)) == [page]  # once

    # The owner protects it with a GTC stop in TWS (client 0): the next book refresh covers it.
    tws = stop_leg(0, 100, 45.0, parent=0, ref="", client=0)
    broker.book.append((0, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), tws, "PreSubmitted", 100.0))
    clock.advance(31)
    bridge.decisions = lambda: [decision_row("d2", "NVDA", created=clock.value, con_id=4933)]
    bridge.tick()
    assert bridge.brackets["d2"].status == "sent"
    assert bridge.account_open_risk().total == Decimal("550")  # 100 x (50.00 avgCost - 45) + the new 50


def test_v7c_unprotected_page_text_never_promises_a_day_stop_unblocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Telegram /stop orders are DAY orders: a page saying "/stop SYM PRICE unblocks" would unblock until the close.
    assert pb.UNPROTECTED_ADVICE.format(symbol="SYM") == (  # "or in TWS" added by v7c fix 1 (N18 LOW-5)
        "close it with /close SYM or in TWS, or protect it with a stop that stays working (GTC)")
    clock = Clock(DECIDED)
    broker = connected_broker(positions={AAPL_ID: 100}, contracts=STOCK)
    bridge = make_bridge(tmp_path, broker, clock=clock)
    capsys.readouterr()
    for _ in range(10):
        bridge.tick()
        clock.advance(2)
    [page] = critical(events(capsys))
    assert page.startswith("v7c_unprotected_position AAPL:") and "(GTC)" in page and "/close AAPL" in page
    assert "/stop" not in page and "/stop" not in pb.UNPROTECTED_ADVICE
    source = BRIDGE_PATH.read_text(encoding="utf-8")
    assert "add /stop" not in source and "/stop SYM PRICE to unblock" not in source


def test_v7c_two_decisions_one_tick_second_skipped(tmp_path: Path) -> None:
    decisions = (decision_row("d1", "AAPL"), decision_row("d2", "MSFT", con_id=4932))
    bridge, broker, _ = v7c_world(tmp_path, held=9950, decisions=decisions)  # open 19,900: room for one 50.00
    bridge.tick()

    assert bridge.brackets["d1"].status == "sent"
    assert v7c_skip(bridge, "d2") == ("v7c_day_risk_budget: realized 0.00 + open 19950.00 + new 50.00 = 20000.00"
                                      " (3.01%) > 3% of 665000.00 (source=bot)" + WOULD_HAVE)
    assert [sent.orderRef for _, sent in broker.placed] == ["autotrader:MOMO_HOD_V2:d1"] * 3


def test_v7c_reservation_is_counted_before_the_order_leaves(tmp_path: Path) -> None:
    bridge, broker, _ = v7c_world(tmp_path, held=9900, decisions=(decision_row("d1", "AAPL"),))
    seen: list[tuple[bool, Decimal]] = []
    original = broker.placeOrder

    def place(order_id: int, contract: Any, sent: Any) -> None:
        if not seen:  # the moment the first order of the bracket leaves
            seen.append(("d1" in bridge.brackets, bridge.account_open_risk().total))
        original(order_id, contract, sent)

    broker.placeOrder = place
    bridge.tick()
    assert seen == [(True, Decimal("19850"))]  # 19,800 + its own 50: reserved before placeOrder


def test_v7c_check_after_quote_wait(tmp_path: Path) -> None:
    bridge, broker, _ = v7c_world(tmp_path, held=9900, decisions=(decision_row("d1", "AAPL"),))  # 19,850 fits
    original = broker.reqMktData

    def owner_widens_during_the_quote_wait(request_id: int, contract: Any, *args: Any) -> None:
        broker.openOrder(32, SimpleNamespace(symbol="OPEN", conId=300), stop_leg(32, 9900, 47.0, parent=30),
                         SimpleNamespace(status="PreSubmitted"))
        original(request_id, contract, *args)

    broker.reqMktData = owner_widens_during_the_quote_wait
    bridge.tick()

    assert broker.placed == []  # checked after the wait: 9,900 x 3.00 = 29,700 is over 19,950
    assert v7c_skip(bridge, "d1").startswith("v7c_day_risk_budget: realized 0.00 + open 29700.00 + new 50.00")


def test_v7c_factor_scales_open_and_new(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(pb, "STOP_SLIPPAGE_FACTOR", Decimal("1.5"))
    bridge, _, _ = v7c_world(tmp_path, held=1000, decisions=(decision_row("d1", "AAPL"),))
    capsys.readouterr()
    bridge.tick()

    assert ("INFO", "v7c_budget_ok AAPL (MOMO_HOD_V2): realized 0.00 + open 3000.00 + new 75.00 = 3075.00 (0.46%)"
            " <= 3% of 665000.00 (source=bot)") in events(capsys)  # 1,000 x 2 x 1.5 and 100 x 0.5 x 1.5
    assert pb.budget_line().endswith("stop slippage factor 1.5)")


def test_v7c_wrong_thread_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bridge = make_bridge(tmp_path, connected_broker())
    result: dict[str, Any] = {}
    reader = threading.Thread(target=lambda: result.update(problem=bridge.budget_problem(Decimal("50"))),
                              name="ibapi-reader")
    capsys.readouterr()
    reader.start()
    reader.join()

    assert result["problem"] == "v7c_wrong_thread: checked off the bridge loop — refused (source=bot)"
    assert critical(events(capsys)) == [
        "v7c_wrong_thread: the day risk budget was checked on thread ibapi-reader, not the bridge loop — refused"
        " (source=bot)"
    ]
    assert bridge.budget_problem(Decimal("50")) is None  # the loop's own thread


def test_v7c_invariant_tripwire_fires_when_open_risk_grows_more_than_the_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = make_bridge(tmp_path, connected_broker(bid=Decimal("50.10")), decisions=(decision_row("d1", "AAPL"),))
    totals = iter([Decimal("0"), Decimal("0"), Decimal("120")])  # gate, budget check, after the order is stored
    real = bridge.account_open_risk
    bridge.account_open_risk = lambda: replace(real(), total=next(totals))
    capsys.readouterr()
    bridge.tick()

    assert bridge.brackets["d1"].status == "sent"
    assert ("CRITICAL", "v7c_budget_invariant_broken: open risk grew by 120.00 with AAPL (MOMO_HOD_V2), more than"
            " its own 50.00 (0.00 -> 120.00) — check TWS") in events(capsys)


def test_v7c_daily_loss_2pct_account_level_blocks_manual_buy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 TSLA stop 185", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.decisions = lambda: [decision_row("d1", "AAPL", created=MANUAL_T0)]
    # The owner's own trades lost 13,300 today (IBKR's account realized P&L): no bot bracket lost anything.
    broker.pnl(pb.PNL_REQUEST_ID, -13300.0, 0.0, -13300.0)
    bridge.tick()

    limit = ("daily_loss_limit: daily loss limit reached (account realized loss 13300.00 >= 2% of net liquidation"
             " 665000.00)")
    assert v7c_skip(bridge, "d1") == limit  # O14: the bot's realized P&L is 0, the account's -2 %
    row = manual_row(row_id)
    assert (row.status, row.reject_code, row.error) == (
        "rejected", "daily_loss_limit", f"{limit} (source=manual). Nothing sent.")
    assert broker.placed == []


EXIT_TEXTS = ["/sell 30 AAPL", "/sell 30 AAPL 191", "/stop 50 AAPL at 185", "/close AAPL", "/sell all AAPL"]


@pytest.mark.parametrize("text", EXIT_TEXTS)
def test_v7c_exits_never_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    # The worst account: past the -2 % block, and 100 AAPL without any stop (every buy refused).
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual(text, message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    broker.pnl(pb.PNL_REQUEST_ID, -20000.0, 0.0, -20000.0)
    assert bridge.budget_problem(Decimal("1")).startswith("daily_loss_limit")
    bridge.tick()

    [(_, sent)] = broker.placed
    assert (sent.action, sent.orderRef) == ("SELL", f"manual:{row_id}")
    assert manual_row(row_id).status == "sent"


def test_v7c_bot_exits_closeall_and_cancels_never_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    clock = Clock(ny(15, 44, 30))  # before the flatten guard: owner rows are still claimed
    working = queue_manual("/sell 30 AAPL 191", message_id=1, owner_shares=100, now=clock.value)
    held = bracket("held", "HELD", status="open", parent=30, filled=40, parent_cancel_requested=True)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100, 300: 40},
                              contracts=STOCK, live_orders={30: "Cancelled", 31: "Submitted", 32: "PreSubmitted"},
                              executions=(execution("p", 30, 40, 50.0),))
    broker.reqOpenOrders()
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"held": held}, clock=clock)
    broker.pnl(pb.PNL_REQUEST_ID, -20000.0, 0.0, -20000.0)
    bridge.tick()  # the resize of the dead partial entry and the owner's sell both go out
    assert [(order_id, sent.totalQuantity) for order_id, sent in broker.placed[:2]] == [(31, 40), (32, 40)]
    assert manual_row(working).status == "sent"

    sell_order = next(iter(bridge.manual.tracked[working].order_ids))
    queue_manual(f"/cancel {working[:6]}", message_id=2, now=clock.value)
    close_all = queue_manual("/closeall", message_id=3, now=clock.value)
    clock.value = ny(15, 45, 0)
    broker.placed.clear()
    bridge.tick()  # the 15:45 flatten, the cancel and the /closeall

    assert ("autotrader:MOMO_HOD_V2:held", "LMT") in [(sent.orderRef, sent.orderType) for _, sent in broker.placed]
    assert broker.cancelled == [sell_order]
    assert manual_row(close_all).status == "sent"  # it started closing the bot's bracket; no v7c refusal
    assert bridge.budget_problem(Decimal("1")).startswith("daily_loss_limit")  # still blocked for new risk


def test_v7c_manual_buy_without_stop_refused_by_parser_and_db_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading.execution import manual_orders as mo

    refusal = mo.parse_order_text("/buy 10 AAPL")
    assert isinstance(refusal, mo.ParseRefusal) and refusal.code == "missing_entry_stop"
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    with closing(sqlite3.connect(ledger)) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(autotrader_manual_orders)")]
        copied = {"id": "'f' || substr(id, 2)", "message_id": "message_id + 100", "update_id": "update_id + 100",
                  "entry_stop_price": "NULL"}
        with pytest.raises(sqlite3.IntegrityError, match="manual_buy_needs_stop"):  # migration 17's trigger
            connection.execute(
                f"INSERT INTO autotrader_manual_orders ({', '.join(columns)}) SELECT"
                f" {', '.join(copied.get(name, name) for name in columns)} FROM autotrader_manual_orders WHERE id=?",
                (row_id,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE autotrader_manual_orders SET entry_stop_price=NULL WHERE id=?", (row_id,))
        # A buy recorded before migration 17 has no stop: simulate one (the triggers are v17's own).
        connection.execute("DROP TRIGGER autotrader_manual_orders_entry_stop_immutable")
        connection.execute("UPDATE autotrader_manual_orders SET entry_stop_price=NULL WHERE id=?", (row_id,))
        connection.commit()
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()

    assert broker.placed == []  # never sent without its stop
    assert (manual_row(row_id).status, manual_row(row_id).reject_code) == ("rejected", "missing_entry_stop")


def test_v7c_manual_buy_over_budget_refused_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 TSLA stop 185", message_id=1)  # 10 x (190.01 - 185) = 50.10
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    item = bracket("open", "OPEN", status="open", parent=30, filled=9950, qty=9950)
    item.stop = "48.00"
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={300: 9950},
                              live_orders={31: "Submitted", 32: "PreSubmitted"},
                              order_objects={32: stop_leg(32, 9950, 48.0, parent=30)})
    broker.reqOpenOrders()
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"open": item}, clock=clock)  # open 19,900
    capsys.readouterr()
    bridge.tick()

    numbers = ("v7c_day_risk_budget: realized 0.00 + open 19900.00 + new 50.10 = 19950.10 (3.00%) > 3% of 665000.00"
               " (source=manual)")
    row = manual_row(row_id)
    assert (row.status, row.reject_code, row.error) == ("rejected", "v7c_day_risk_budget", f"{numbers}. Nothing sent.")
    assert manual_lines(events(capsys)) == [
        f"REJECTED BUY 10 TSLA MKT #{row_id[:6]}: {numbers}. Nothing sent. (v7c_day_risk_budget)"]
    broker.openOrder(32, SimpleNamespace(symbol="OPEN", conId=300), stop_leg(32, 9950, 50.0, parent=30),
                     SimpleNamespace(status="PreSubmitted"))  # the budget frees up: the refusal stays final
    clock.advance(2)
    bridge.tick()
    assert broker.placed == [] and manual_row(row_id).status == "rejected"


@pytest.mark.parametrize(("text", "entry", "stop_price", "new_risk"), [
    ("/buy 10 AAPL stop 185", ("MKT", None), 185.0, "50.10"),  # sized at the ask 190.01
    ("/buy 10 AAPL 185 stop 180", ("LMT", 185.0), 180.0, "50.00"),  # the limit
])
def test_v7c_manual_buy_sends_attached_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    text: str, entry: tuple[str, float | None], stop_price: float, new_risk: str,
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual(text, message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    capsys.readouterr()
    bridge.tick()

    [(parent_id, sent), (stop_id, stop)] = broker.placed
    order_type, limit = entry
    assert (sent.action, sent.orderType, sent.totalQuantity, sent.tif, sent.parentId, sent.transmit) == (
        "BUY", order_type, 10, "DAY", 0, False)
    if limit is not None:
        assert sent.lmtPrice == pytest.approx(limit)
    assert (stop_id, stop.action, stop.orderType, stop.totalQuantity, stop.tif, stop.parentId, stop.transmit) == (
        parent_id + 1, "SELL", "STP", 10, "GTC", parent_id, True)
    assert stop.auxPrice == pytest.approx(stop_price)
    assert sent.orderRef == stop.orderRef == f"manual:{row_id}" and stop.account == ACCOUNT
    row = manual_row(row_id)
    assert (row.status, row.ibkr_order_id) == ("sent", parent_id)
    out = events(capsys)
    assert ("INFO", f"v7c_budget_ok AAPL (manual #{row_id[:6]}): realized 0.00 + open 0.00 + new {new_risk} ="
            f" {new_risk} (0.01%) <= 3% of 665000.00 (source=manual)") in out
    assert manual_lines(out) == [f"SENT {row.summary()} #{row_id[:6]} (order {parent_id}) with its stop"
                                 f" {Decimal(str(stop_price)).normalize():f} GTC (order {stop_id})"]
    # Until it fills, the buy itself is open risk: 10 x (entry - stop), and its stop covers what it buys.
    assert bridge.account_open_risk().total == Decimal(new_risk)


def manual_buy_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str = "/buy 10 AAPL 185 stop 180"
                     ) -> tuple[Any, FakeBroker, Clock, str, int, int]:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual(text, message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), contracts=STOCK)
    broker.own_in_all_open_orders = True  # TWS reports the bridge's own working orders to every book read
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(parent_id, _), (stop_id, _)] = broker.placed
    return bridge, broker, clock, row_id, parent_id, stop_id


def test_v7c_attached_stop_fills_do_not_count_as_buy_fills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge, broker, clock, row_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch)
    ref = f"manual:{row_id}"
    broker.execDetails(-1, None, execution("0000e0d5.000000f1.01.01", parent_id, 4, 185.0, acctNumber=ACCOUNT,
                                           orderRef=ref, side="BOT", time="20261013  10:00:30"))
    broker.orderStatus(parent_id, "Submitted", 4, 6, 185.0)
    clock.advance(2)
    bridge.tick()
    assert (manual_row(row_id).status, manual_row(row_id).filled_qty) == ("partially_filled", 4)

    # The price falls through 180: the attached stop sells the 4 shares (same orderRef, side SLD).
    broker.execDetails(-1, None, execution("0000e0d5.000000f2.01.01", stop_id, 4, 179.9, acctNumber=ACCOUNT,
                                           orderRef=ref, side="SLD", time="20261013  10:01:00"))
    broker.orderStatus(stop_id, "Submitted", 4, 6, 179.9)
    clock.advance(2)
    bridge.tick()
    assert (manual_row(row_id).status, manual_row(row_id).filled_qty) == ("partially_filled", 4)  # never 8

    broker.execDetails(-1, None, execution("0000e0d5.000000f3.01.01", parent_id, 6, 185.0, acctNumber=ACCOUNT,
                                           orderRef=ref, side="BOT", time="20261013  10:02:00"))
    broker.orderStatus(parent_id, "Filled", 10, 0, 185.0)
    clock.advance(2)
    bridge.tick()
    row = manual_row(row_id)
    assert (row.status, row.filled_qty, row.avg_fill_price) == ("filled", 10, Decimal("185.0000"))


@pytest.mark.parametrize("text", ["/close AAPL", "/sell all AAPL"])
def test_v7c_close_of_a_manual_buy_cancels_its_own_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch,
                                                                         "/buy 10 AAPL stop 185")
    fill(broker, parent_id, 10, 190.01, f"manual:{buy_id}", "0000e0d5.000000f4.01.01")
    push_position(broker, AAPL_ID, 10)
    clock.advance(2)
    bridge.tick()
    assert manual_row(buy_id).status == "filled"
    close_id = queue_manual(text, message_id=2, now=clock.value, owner_shares=10)
    broker.placed.clear()
    capsys.readouterr()
    clock.advance(1)
    bridge.tick()

    # First the buy's own stop is cancelled: two sells must never work on the same 10 shares.
    assert broker.placed == [] and broker.cancelled == [stop_id]
    assert manual_row(close_id).status == "sending"
    summary = manual_row(close_id).summary()
    assert manual_lines(events(capsys)) == [f"RELEASE {summary} #{close_id[:6]}: cancelling your attached stop(s)"
                                            f" [{stop_id}] first; the sell follows once IBKR confirms"]
    clock.advance(2)
    bridge.tick()
    assert broker.placed == []  # IBKR has not confirmed yet: still nothing sold

    broker.orderStatus(stop_id, "Cancelled", 0, 10, 0)
    clock.advance(2)
    bridge.tick()
    [(_, sell)] = broker.placed
    assert (sell.action, sell.orderType, sell.totalQuantity, sell.orderRef) == ("SELL", "MKT", 10, f"manual:{close_id}")
    assert manual_row(close_id).status == "sent"


def test_v7c_a_partial_sell_leaves_the_attached_stop_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch,
                                                                         "/buy 10 AAPL stop 185")
    fill(broker, parent_id, 10, 190.01, f"manual:{buy_id}", "0000e0d5.000000f5.01.01")
    push_position(broker, AAPL_ID, 10)
    clock.advance(2)
    bridge.tick()
    sell_id = queue_manual("/sell 5 AAPL", message_id=2, now=clock.value, owner_shares=10)
    broker.placed.clear()
    clock.advance(1)
    bridge.tick()

    # Only /close, /sell all and /closeall release it; a partial sell meets the stop's reservation (open question).
    assert broker.placed == [] and broker.cancelled == []
    assert manual_row(sell_id).reject_code == "insufficient_shares"


def test_v7c_closeall_cancels_the_owners_attached_stops_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch,
                                                                         "/buy 10 AAPL stop 185")
    fill(broker, parent_id, 10, 190.01, f"manual:{buy_id}", "0000e0d5.000000f6.01.01")
    push_position(broker, AAPL_ID, 10)
    clock.advance(2)
    bridge.tick()
    close_id = queue_manual("/closeall", message_id=2, now=clock.value)
    broker.placed.clear()
    clock.advance(1)
    bridge.tick()
    assert broker.placed == [] and broker.cancelled == [stop_id]

    broker.orderStatus(stop_id, "Cancelled", 0, 10, 0)
    clock.advance(2)
    bridge.tick()
    [(_, sell)] = broker.placed
    assert (sell.action, sell.orderType, sell.totalQuantity, sell.orderRef) == ("SELL", "MKT", 10, f"manual:{close_id}")


def test_v7c_an_unconfirmed_stop_cancel_refuses_the_close_and_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch,
                                                                         "/buy 10 AAPL stop 185")
    fill(broker, parent_id, 10, 190.01, f"manual:{buy_id}", "0000e0d5.000000f7.01.01")
    push_position(broker, AAPL_ID, 10)
    clock.advance(2)
    bridge.tick()
    close_id = queue_manual("/close AAPL", message_id=2, now=clock.value, owner_shares=10)
    broker.placed.clear()
    clock.advance(1)
    bridge.tick()
    capsys.readouterr()
    clock.advance(11)  # IBKR never reports the stop cancelled
    bridge.tick()

    assert broker.placed == []
    message = (f"IBKR did not confirm the cancel of your attached stop(s) [{stop_id}] in 10s, so nothing was sold;"
               " check TWS: the stop may be gone. Nothing sent.")
    row = manual_row(close_id)
    assert (row.status, row.reject_code, row.error) == ("rejected", "entry_stop_release_unconfirmed", message)
    assert f"manual order #{close_id[:6]} (CLOSE 10 AAPL MKT): {message}" in critical(events(capsys))


def test_v7c_close_refused_while_its_buy_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch)
    broker.execDetails(-1, None, execution("0000e0d5.000000f8.01.01", parent_id, 4, 185.0, acctNumber=ACCOUNT,
                                           orderRef=f"manual:{buy_id}", side="BOT", time="20261013  10:00:30"))
    broker.orderStatus(parent_id, "Submitted", 4, 6, 185.0)
    push_position(broker, AAPL_ID, 4)
    clock.advance(2)
    bridge.tick()
    close_id = queue_manual("/close AAPL", message_id=2, now=clock.value, owner_shares=4)
    broker.placed.clear()
    clock.advance(1)
    bridge.tick()

    assert broker.placed == [] and broker.cancelled == []  # the stop keeps protecting what the buy may still fill
    row = manual_row(close_id)
    assert (row.reject_code, row.error) == ("manual_buy_still_working", (
        f"Your buy of AAPL (order {parent_id}) still works and its stop protects what it buys; /cancel that buy"
        " first, then send this again. Nothing sent."))


def test_v7c_cancel_of_a_partly_filled_buy_keeps_and_fits_its_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch)
    broker.execDetails(-1, None, execution("0000e0d5.000000f9.01.01", parent_id, 4, 185.0, acctNumber=ACCOUNT,
                                           orderRef=f"manual:{buy_id}", side="BOT", time="20261013  10:00:30"))
    broker.orderStatus(parent_id, "Submitted", 4, 6, 185.0)
    push_position(broker, AAPL_ID, 4)
    queue_manual(f"/cancel {buy_id[:6]}", message_id=2, now=clock.value)
    clock.advance(2)
    bridge.tick()
    assert broker.cancelled == [parent_id]  # the entry only: never the stop protecting the 4 bought

    broker.orderStatus(parent_id, "Cancelled", 4, 6, 185.0)
    broker.placed.clear()
    capsys.readouterr()
    clock.advance(2)
    bridge.tick()
    assert (manual_row(buy_id).status, manual_row(buy_id).filled_qty) == ("cancelled", 4)
    assert broker.placed == []  # fitted only once the fills and the position have settled
    clock.advance(16)
    bridge.tick()

    [(order_id, fitted)] = broker.placed
    assert (order_id, fitted.action, fitted.orderType, fitted.totalQuantity, fitted.tif, fitted.parentId) == (
        stop_id, "SELL", "STP", 4, "GTC", parent_id)
    assert fitted.auxPrice == pytest.approx(180.0) and fitted.orderRef == f"manual:{buy_id}"
    assert f"STOP FITTED AAPL order {stop_id}: 10 -> 4 sh at 180.0, the shares its buy still holds" in manual_lines(
        events(capsys))
    for _ in range(5):  # TWS echoed it: done, no alarm, never re-sent
        clock.advance(2)
        bridge.tick()
    assert len(broker.placed) == 1 and not critical(events(capsys))


def test_v7c_a_dead_unfilled_buy_has_its_stop_cancelled_if_ibkr_did_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch)
    broker.orderStatus(parent_id, "Cancelled", 0, 10, 0)  # e.g. cancelled in TWS; the child still shows working
    clock.advance(2)
    bridge.tick()
    assert manual_row(buy_id).status == "cancelled" and broker.cancelled == []
    clock.advance(16)
    bridge.tick()
    assert broker.cancelled == [stop_id]


def test_v7c_avg_cost_stored_from_positions() -> None:
    import sys as system

    state = pb.BrokerState(CLIENT_ID)
    contract = SimpleNamespace(conId=AAPL_ID, symbol="AAPL")
    state.position(ACCOUNT, contract, 100.0, 187.25)
    assert state.position_avg_cost[(ACCOUNT, AAPL_ID)] == Decimal("187.25")
    assert state.position_basis[(ACCOUNT, AAPL_ID)] == Decimal("187.25")
    state.position(ACCOUNT, contract, 0.0, 0.0)  # sold out: no cost now, the basis stays for its executions
    assert (ACCOUNT, AAPL_ID) not in state.position_avg_cost
    assert state.position_basis[(ACCOUNT, AAPL_ID)] == Decimal("187.25")
    state.position(ACCOUNT, contract, 50.0, system.float_info.max)  # 9.81's UNSET is never a cost
    assert (ACCOUNT, AAPL_ID) not in state.position_avg_cost
    state.execDetails(-1, contract, execution("e1", 7, 50, 180.0, acctNumber=ACCOUNT, orderRef="manual:x", side="SLD"))
    assert next(iter(state.execs.values())).basis == Decimal("187.25")  # stamped as it arrives


def test_v7c_tws_stop_seen_through_all_open_orders(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(bid=Decimal("50.10"), positions={AAPL_ID: 100}, contracts=STOCK)
    broker.position(ACCOUNT, STOCK[AAPL_ID], 100.0, 52.5)  # IBKR's avgCost, commissions included
    tws = stop_leg(5, 100, 49.0, parent=0, ref="", client=0)  # the owner's own stop, placed in TWS
    tws.tif = "DAY"
    broker.book.append((5, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), tws, "PreSubmitted", 100.0))
    bridge = make_bridge(tmp_path, broker, clock=clock, decisions=(decision_row("d1", "MSFT", con_id=4932),))
    capsys.readouterr()
    bridge.tick()

    [item] = bridge.order_book
    assert (item.client_id, item.action, item.order_type, item.aux_price, item.parent_id, item.open_quantity) == (
        0, "SELL", "STP", Decimal("49.0"), 0, Decimal("100"))
    assert bridge.brackets["d1"].status == "sent"  # covered: a working stop, whoever placed it
    assert bridge.account_open_risk().lines[-1] == "AAPL account 100 held @ 52.5, stop 49.0: 350.00"

    broker.book.clear()  # the owner cancelled it in TWS: the next refresh no longer covers the shares
    clock.advance(31)
    bridge.decisions = lambda: [decision_row("d2", "NVDA", created=clock.value, con_id=4933)]
    bridge.tick()
    assert v7c_skip(bridge, "d2").startswith("v7c_unprotected_position: AAPL 100 sh held, working stops cover 0")


def test_v7c_a_stale_book_covers_nothing(tmp_path: Path) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(positions={AAPL_ID: 100}, contracts=STOCK)
    broker.book.append((5, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), stop_leg(5, 100, 49.0, parent=0, ref="",
                                                                                     client=0), "PreSubmitted", 100.0))
    bridge = make_bridge(tmp_path, broker, clock=clock)
    bridge.tick()
    assert bridge.account_open_risk().unprotected == ()
    broker.reqAllOpenOrders = lambda: None  # TWS stops answering: no refresh completes any more
    clock.advance(91)
    bridge.tick()
    assert bridge.account_open_risk().unprotected == (
        ("AAPL", "100 sh held, working stops cover 0 (no fresh open-orders book: stops placed in TWS are unknown)"),)


def test_v7c_pnl_source_reqpnl_then_computed_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys as system

    broker = connected_broker()
    bridge = make_bridge(tmp_path, broker)
    bridge.startup()
    assert broker.pnl_requests == [(pb.PNL_REQUEST_ID, ACCOUNT, "")]
    # An owner sell of 100 AAPL bought at 200.00 (IBKR's avgCost when it arrived) for 190.00: -1,000.
    broker.position(ACCOUNT, STOCK[AAPL_ID], 100.0, 200.0)
    broker.execDetails(-1, STOCK[AAPL_ID], execution("0000e0d5.000000aa.01.01", 9, 100, 190.0, acctNumber=ACCOUNT,
                                                       orderRef="manual:abc", side="SLD", time="20261013  10:00:30"))
    capsys.readouterr()

    assert bridge.account_realized_loss() == Decimal("1000")
    broker.pnl(pb.PNL_REQUEST_ID, -1500.0, 0.0, -1500.0)  # IBKR also counts commissions and TWS-direct trades
    assert bridge.account_realized_loss() == Decimal("1500")
    assert bridge.account_realized_loss() == Decimal("1500")
    # Changed by v7c fix 1 (N18 LOW-2, owner Q5 "the floor now"): an unset report or a stale value
    # switches the source to computed, but IBKR's last value stays a lower bound for the loss (was 1000).
    for unset in (None, float("nan"), float("inf"), system.float_info.max):
        broker.pnl(pb.PNL_REQUEST_ID, 0.0, 0.0, unset)
        assert bridge.account_realized_loss() == Decimal("1500"), unset
    broker.pnl(pb.PNL_REQUEST_ID, 0.0, 0.0, -1010.0)
    assert bridge.account_realized_loss() == Decimal("1010")  # within $25 of the computed: no WARN
    monkeypatch.setattr(pb, "PNL_FRESH", timedelta(seconds=-1))  # older than 120 s: stale
    assert bridge.account_realized_loss() == Decimal("1010")  # the floor (was 1000: v7c fix 1, LOW-2)
    monkeypatch.undo()
    broker.pnl(pb.PNL_REQUEST_ID, 0.0, 0.0, 2500.0)  # IBKR says +2,500: its value never hides the computed loss
    assert bridge.account_realized_loss() == Decimal("1000")
    bridge.app.execs.clear()  # nothing computed: a winning day funds nothing
    assert bridge.account_realized_loss() == 0

    out = [text for _, text in events(capsys)]
    assert [line for line in out if line.startswith("v7c_pnl_source")] == [
        "v7c_pnl_source=computed", "v7c_pnl_source=reqpnl", "v7c_pnl_source=computed", "v7c_pnl_source=reqpnl",
        "v7c_pnl_source=computed", "v7c_pnl_source=reqpnl",
    ]  # once per change
    stale = [line for line in out if line.startswith("v7c_pnl_stale")]  # once per stale episode (LOW-2)
    assert stale[0] == "v7c_pnl_stale: IBKR's last realized -1500.00 is no longer reported — it stays a lower bound" \
        " for today's loss"
    assert len(stale) == 2 and re.fullmatch(r"v7c_pnl_stale: IBKR's last realized -1010\.00 is \d+s old — it stays a"
                                            r" lower bound for today's loss", stale[1])
    assert [line for line in out if "v7c_pnl_mismatch" in line] == [
        "WARN v7c_pnl_mismatch: IBKR realized -1500.00 vs computed -1000.00 (gap 500.00 > 332.50); the budget"
        " counts the larger loss",
        "WARN v7c_pnl_mismatch: IBKR realized 2500.00 vs computed -1000.00 (gap 3500.00 > 332.50); the budget"
        " counts the larger loss",
    ]
    assert not [line for line in out if ACCOUNT in line]  # the account id is never logged


def test_v7c_over_budget_pages_once_per_half_percent_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    bridge, broker, _ = v7c_world(tmp_path, held=10000, clock=clock)  # open 20,000 = 3.01 %
    capsys.readouterr()
    for _ in range(3):
        bridge.tick()
        clock.advance(2)
    assert critical(events(capsys)) == [
        "v7c_budget_over: realized 0.00 + open 20000.00 + new 0.00 = 20000.00 (3.01%) > 3% of 665000.00 — no new"
        " entries; never flattened"
    ]
    broker.openOrder(32, SimpleNamespace(symbol="OPEN", conId=300), stop_leg(32, 10000, 47.6, parent=30),
                     SimpleNamespace(status="PreSubmitted"))  # 24,000 = 3.61 %: the next 0.5 % step
    bridge.tick()
    bridge.tick()
    assert [line for line in critical(events(capsys)) if line.startswith("v7c_budget_over")] == [
        "v7c_budget_over: realized 0.00 + open 24000.00 + new 0.00 = 24000.00 (3.61%) > 3% of 665000.00 — no new"
        " entries; never flattened"
    ]
    assert broker.placed == [] and broker.cancelled == []  # nothing is ever flattened because of the budget


@pytest.mark.parametrize(("shown", "sent"), [(49.8, 49.8), (49.0, 49.5)])
def test_v7c_resize_keeps_owner_raised_stop(tmp_path: Path, shown: float, sent: float) -> None:
    held = bracket("held", "HELD", status="open", parent=10, filled=40, parent_cancel_requested=True)
    broker = connected_broker(executions=(execution("p", 10, 40, 50.0),), positions={100: 40},
                              live_orders={10: "Cancelled", 11: "Submitted", 12: "PreSubmitted"},
                              order_objects={12: stop_leg(12, 100, shown, parent=10, ref="autotrader:MOMO_HOD_V2:held")})
    broker.reqOpenOrders()
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    bridge = make_bridge(tmp_path, broker, brackets={"held": held})
    bridge.refresh(held)
    bridge.resize_children(held)

    # C2: the owner raised the stop to 49.80 in TWS; a resize never puts it back to the intent's 49.50.
    # A stop the owner widened goes back up to the intent (the higher of the two, for a long).
    stops = [(order_id, item.totalQuantity, item.auxPrice) for order_id, item in broker.placed if item.orderType == "STP"]
    assert stops == [(12, 40, pytest.approx(sent))]


def test_no_override_path() -> None:
    # D5: no same-day override of the day risk budget or the -2 % block, by any word, flag or code path.
    backend = BRIDGE_PATH.parents[1]
    forbidden = re.compile(r"override|--force|i-know|i_know|force=True", re.IGNORECASE)
    near = re.compile(r"budget|v7c|daily_loss|DAILY_LOSS|entry_stop|unprotected", re.IGNORECASE)
    for path in (BRIDGE_PATH, backend / "trading/execution/manual_orders.py", backend / "trading/operations/telegram_bot.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not near.search(line):
                continue
            window = lines[max(0, index - 10): index + 11]
            hits = [text for text in window if forbidden.search(text)]
            assert hits == [], f"{path.name}:{index + 1}: {hits}"


def test_v7c_a_buy_whose_stop_lowers_a_held_positions_stop_counts_that_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 100 AAPL held at avgCost 190 under the owner's TWS stop at 189 (risk 100). A buy of 10 more with its
    # stop at 185 makes 185 the lowest stop on all 110 shares: 10 x 5.01 + 100 x 4 = 450.10 new risk.
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    broker.position(ACCOUNT, STOCK[AAPL_ID], 100.0, 190.0)
    broker.book.append((7, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), stop_leg(7, 100, 189.0, parent=0, ref="",
                                                                                     client=0), "PreSubmitted", 100.0))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    capsys.readouterr()
    bridge.tick()

    out = events(capsys)
    assert ("INFO", f"v7c_budget_ok AAPL (manual #{row_id[:6]}): realized 0.00 + open 100.00 + new 450.10 = 550.10"
            " (0.08%) <= 3% of 665000.00 (source=manual)") in out
    assert [sent.action for _, sent in broker.placed] == ["BUY", "SELL"]
    assert bridge.account_open_risk().total == Decimal("550.10")  # what was checked is what is held
    assert not critical(out)  # the invariant tripwire stays quiet: no growth beyond the checked risk


def test_v7c_a_close_the_freed_stop_would_not_help_cancels_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 100 AAPL under the owner's TWS stop, plus 10 bought with their attached stop. A /close of 110 still
    # meets the TWS stop's 100 reserved shares: the attached stop must not be cancelled for nothing.
    ledger = manual_ledger(tmp_path, monkeypatch)
    buy_id = queue_manual("/buy 10 AAPL stop 185", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    broker.own_in_all_open_orders = True
    broker.book.append((7, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), stop_leg(7, 100, 189.0, parent=0, ref="",
                                                                                     client=0), "PreSubmitted", 100.0))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(parent_id, _), (stop_id, _)] = broker.placed
    fill(broker, parent_id, 10, 190.01, f"manual:{buy_id}", "0000e0d5.000000fa.01.01")
    push_position(broker, AAPL_ID, 110)
    clock.advance(2)
    bridge.tick()
    close_id = queue_manual("/close AAPL", message_id=2, now=clock.value, owner_shares=110)
    broker.placed.clear()
    clock.advance(1)
    bridge.tick()

    assert broker.placed == [] and broker.cancelled == []
    assert manual_row(close_id).reject_code == "insufficient_shares"


def test_v7c_connect_and_preflight_line(bridge_env: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    from trading.operations.deploy_apply import BUDGET_TEXT

    assert pb.budget_line() == V7C_BUDGET_LINE
    assert V7C_BUDGET_LINE.startswith(BUDGET_TEXT)  # deploy apply's dry_run_budget check reads this prefix
    bridge_env.install(None, Clock(ny(10, 0)))  # --preflight never connects
    assert pb.main(["--preflight"]) == pb.EXIT_OK
    assert [text for _, text in events(capsys) if BUDGET_TEXT in text] == [V7C_BUDGET_LINE]  # exactly once
    broker = FakeBroker()
    broker.disconnect_after = 2
    bridge_env.install(broker, Clock(ny(10, 0)))
    assert pb.main(["--date", "2026-10-13"]) == pb.EXIT_RESTART
    assert [text for _, text in events(capsys) if BUDGET_TEXT in text] == [V7C_BUDGET_LINE]


def test_v7c_manual_buy_is_reserved_before_it_leaves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    queue_manual("/buy 10 AAPL 185 stop 180", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    seen: list[Decimal] = []
    original = broker.placeOrder

    def place(order_id: int, contract: Any, sent: Any) -> None:
        if not seen:  # the moment the buy leaves, before its stop
            seen.append(bridge.account_open_risk().total)
        original(order_id, contract, sent)

    broker.placeOrder = place
    bridge.tick()
    assert seen == [Decimal("50.00")]  # 10 x (185 - 180): already the account's risk when it is placed


def test_v7c_a_failing_stop_fit_never_blocks_owner_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/sell 30 AAPL", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))

    def broken() -> None:
        raise ValueError("corrupt fit")

    bridge.manual.tend_stop_fits = broken
    capsys.readouterr()
    bridge.tick()
    bridge.tick()

    assert manual_row(row_id).status == "sent"  # the owner's sell still went out
    assert critical(events(capsys)) == [
        "manual-order stop fitting failed (ValueError: corrupt fit) — check the owner's attached stops in TWS"
    ]  # once


# ---- v7c fix 1: review N18 (MEDIUM-1..3, LOW-1..5, 7, 8, 9) ------------------------------------------------------


def _filled_manual_buy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_id: str
                       ) -> tuple[Any, FakeBroker, Clock, str, int, int]:
    """A filled /buy 10 AAPL stop 185 whose position update has arrived: 10 AAPL under the attached stop."""
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch, "/buy 10 AAPL stop 185")
    fill(broker, parent_id, 10, 190.01, f"manual:{buy_id}", exec_id)
    push_position(broker, AAPL_ID, 10)
    clock.advance(2)
    bridge.tick()
    assert manual_row(buy_id).status == "filled"
    return bridge, broker, clock, buy_id, parent_id, stop_id


def unsold_page(row_id: str, summary: str, stop_id: int, what: str, why: str) -> str:
    return (f"manual order #{row_id[:6]} ({summary}): your stop on AAPL (order(s) [{stop_id}]) {what} and AAPL was not"
            f" sold ({why}) — it has no stop; act in TWS")


# MEDIUM-1 (a): an exit decides first and pages by name whenever a released holding is not sold.

def test_fix1_closeall_keeps_the_stop_of_a_holding_it_cannot_sell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18 probe P1: e63a599 cancelled the stop first and then refused the 1.06 % book: no stop, nothing sold.
    bridge, broker, clock, _, _, stop_id = _filled_manual_buy(tmp_path, monkeypatch, "0000e0d5.000001a1.01.01")
    close_id = queue_manual("/closeall", message_id=2, now=clock.value)
    broker.placed.clear()
    broker.quote = Decimal("188.00")  # the book is now 1.06 % wide (a thin name, or a fast market)
    capsys.readouterr()
    clock.advance(1)
    bridge.tick()
    clock.advance(2)
    bridge.tick()

    assert broker.cancelled == [] and broker.placed == []  # the stop still protects the 10 shares
    row = manual_row(close_id)
    assert (row.status, row.reject_code, row.error) == (
        "rejected", "nothing_closed", "AAPL: spread 1.06% over 1% — not sold; its stop is kept")
    assert bridge.account_open_risk().unprotected == ()
    assert not critical(events(capsys))


def test_fix1_closeall_releases_and_sells_only_the_holding_it_can_sell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    aapl = queue_manual("/buy 10 AAPL stop 185", message_id=1)
    tsla = queue_manual("/buy 10 TSLA stop 185", message_id=2)
    broker = per_contract_broker(contracts=STOCK, books={AAPL_ID: (189.99, 190.01), TSLA_ID: (249.99, 250.01)})
    broker.own_in_all_open_orders = True
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    clock.advance(1)
    bridge.tick()  # one owner row per tick
    [(aapl_buy, _), (aapl_stop, _), (tsla_buy, _), (tsla_stop, _)] = broker.placed
    fill(broker, aapl_buy, 10, 190.01, f"manual:{aapl}", "0000e0d5.000001a2.01.01")
    fill(broker, tsla_buy, 10, 250.01, f"manual:{tsla}", "0000e0d5.000001a3.01.01")
    push_position(broker, AAPL_ID, 10)
    push_position(broker, TSLA_ID, 10)
    clock.advance(2)
    bridge.tick()
    assert (manual_row(aapl).status, manual_row(tsla).status) == ("filled", "filled")

    broker.books[AAPL_ID] = (186.00, 190.00)  # AAPL 2.13 % wide; TSLA tight
    close_id = queue_manual("/closeall", message_id=3, now=clock.value)
    broker.placed.clear()
    capsys.readouterr()
    clock.advance(1)
    bridge.tick()
    assert broker.cancelled == [tsla_stop] and broker.placed == []  # only the holding it will sell is released

    broker.orderStatus(tsla_stop, "Cancelled", 0, 10, 0)
    clock.advance(2)
    bridge.tick()
    [(child, sell)] = broker.placed  # the sell leaves only after IBKR confirms its stop's cancel
    assert (sell.action, sell.orderType, sell.totalQuantity) == ("SELL", "MKT", 10)
    assert bridge.manual.tracked[close_id].con_ids == {child: TSLA_ID}
    assert aapl_stop not in broker.cancelled  # the refused holding keeps its stop
    row = manual_row(close_id)
    assert (row.status, row.error) == ("sent", "AAPL: spread 2.13% over 1% — not sold; its stop is kept")
    assert not [text for text in critical(events(capsys)) if "was not sold" in text]


def test_fix1_a_close_whose_quote_turns_during_the_release_pages_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18 probe P1b: the stop is cancelled, then the quote widens before the sell can leave.
    bridge, broker, clock, _, _, stop_id = _filled_manual_buy(tmp_path, monkeypatch, "0000e0d5.000001a4.01.01")
    close_id = queue_manual("/close AAPL", message_id=2, now=clock.value, owner_shares=10)
    broker.placed.clear()
    clock.advance(1)
    bridge.tick()
    assert broker.cancelled == [stop_id]
    broker.orderStatus(stop_id, "Cancelled", 0, 10, 0)
    broker.quote = Decimal("188.00")  # the quote widens in the second it takes IBKR to confirm
    capsys.readouterr()
    clock.advance(1)
    bridge.tick()

    row = manual_row(close_id)
    assert broker.placed == [] and row.status == "rejected"
    pages = critical(events(capsys))
    assert pages == [unsold_page(close_id, "CLOSE 10 AAPL MKT", stop_id, "was cancelled", row.error)]
    assert row.error.startswith("Spread is 1.06% (max 1%)")


@pytest.mark.parametrize("problem", ["wide", "stopping"])
def test_fix1_a_close_checks_its_quote_and_can_send_right_before_it_cancels_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    bridge, broker, clock, _, _, _ = _filled_manual_buy(tmp_path, monkeypatch, "0000e0d5.000001aa.01.01")
    close_id = queue_manual("/close AAPL", message_id=2, now=clock.value, owner_shares=10)
    broker.placed.clear()
    if problem == "wide":
        broker.quote = Decimal("188.00")  # 1.06 % wide when the /close is claimed
    else:
        original = broker.reqMktData

        def quote_then_sigterm(request_id: int, contract: Any, *args: Any) -> None:
            original(request_id, contract, *args)
            bridge.stop_event.set()  # SIGTERM lands during the /close's own quote wait

        broker.reqMktData = quote_then_sigterm  # type: ignore[method-assign]
    clock.advance(1)
    bridge.tick()

    assert broker.cancelled == [] and broker.placed == []  # refused before its stop was touched
    assert manual_row(close_id).status == ("rejected" if problem == "wide" else "error")
    assert bridge.manual.releases == {}


@pytest.mark.parametrize(("how", "code", "why"), [
    ("stop", pb.EXIT_OK, "the bridge stopped"), ("disconnect", pb.EXIT_RESTART, "TWS disconnected")])
def test_fix1_a_release_interrupted_by_a_stop_request_or_a_disconnect_pages_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], how: str, code: int, why: str
) -> None:
    bridge, broker, clock, _, _, stop_id = _filled_manual_buy(tmp_path, monkeypatch, "0000e0d5.000001a5.01.01")
    close_id = queue_manual("/close AAPL", message_id=2, now=clock.value, owner_shares=10)
    clock.advance(1)
    bridge.tick()
    assert broker.cancelled == [stop_id] and close_id in bridge.manual.releases
    if how == "stop":
        bridge.stop_event.set()
    else:
        broker.connected = False
    capsys.readouterr()

    assert bridge.interrupted() == code
    assert unsold_page(close_id, "CLOSE 10 AAPL MKT", stop_id, "is being cancelled (IBKR has not confirmed yet)",
                       f"{why} before IBKR confirmed the cancel") in critical(events(capsys))
    row = manual_row(close_id)
    assert row.status == "error" and row.error == (
        f"{why} while your attached stop(s) [{stop_id}] were being cancelled for it; nothing sold — act in TWS")
    assert bridge.manual.releases == {}


def test_fix1_a_released_sell_stopped_before_it_leaves_pages_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, clock, _, _, stop_id = _filled_manual_buy(tmp_path, monkeypatch, "0000e0d5.000001a6.01.01")
    close_id = queue_manual("/sell all AAPL", message_id=2, now=clock.value, owner_shares=10)
    broker.placed.clear()
    clock.advance(1)
    bridge.tick()
    broker.orderStatus(stop_id, "Cancelled", 0, 10, 0)
    bridge.stop_event.set()  # SIGTERM lands while IBKR confirms the cancel
    capsys.readouterr()
    clock.advance(1)
    bridge.tick()

    assert broker.placed == []
    row = manual_row(close_id)
    assert row.status == "error"
    assert critical(events(capsys)) == [unsold_page(close_id, row.summary(), stop_id, "was cancelled", row.error)]


def test_fix1_a_cancel_of_a_releasing_sell_pages_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, clock, _, _, stop_id = _filled_manual_buy(tmp_path, monkeypatch, "0000e0d5.000001a7.01.01")
    close_id = queue_manual("/close AAPL", message_id=2, now=clock.value, owner_shares=10)
    clock.advance(1)
    bridge.tick()
    queue_manual(f"/cancel {close_id[:6]}", message_id=3, now=clock.value)
    capsys.readouterr()
    clock.advance(1)
    bridge.tick()

    assert manual_row(close_id).status == "cancelled"
    assert critical(events(capsys)) == [unsold_page(
        close_id, "CLOSE 10 AAPL MKT", stop_id, "is being cancelled (IBKR has not confirmed yet)",
        "you cancelled the sell while its stops were being released")]


@pytest.mark.parametrize(("at_ibkr", "state"), [
    ("Cancelled", "stop(s) [{stop}] no longer work at IBKR: AAPL may have no stop"),
    ("PreSubmitted", "IBKR still shows stop(s) [{stop}] working"),
])
def test_fix1_a_restart_reports_a_row_that_was_releasing_stops_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], at_ibkr: str, state: str
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = _filled_manual_buy(tmp_path, monkeypatch,
                                                                           "0000e0d5.000001a8.01.01")
    close_id = queue_manual("/close AAPL", message_id=2, now=clock.value, owner_shares=10)
    clock.advance(1)
    bridge.tick()
    assert manual_row(close_id).status == "sending"  # its audit names the stop being released
    # The bridge dies here (SIGKILL, a crash): the release lived in its memory only. A new bridge starts.
    attached = stop_leg(stop_id, 10, 185.0, parent=parent_id, ref=f"manual:{buy_id}")
    restarted = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 10},
                                 contracts=STOCK, live_orders={stop_id: at_ibkr}, order_objects={stop_id: attached})
    second = desk_bridge(tmp_path, restarted, tmp_path / "ledger.db", clock=clock)  # manual_ledger's database
    capsys.readouterr()
    second.startup()

    message = (f"the bridge stopped while cancelling your attached stop(s) [{stop_id}] on AAPL for it; nothing was"
               f" sold — {state.format(stop=stop_id)}")
    pages = critical(events(capsys))
    assert pages == [f"manual order #{close_id[:6]} (CLOSE 10 AAPL MKT): {message}; marked error, never resent;"
                     " act in TWS"]  # never the plain "marked unknown" line
    row = manual_row(close_id)
    assert (row.status, row.error) == ("error", f"{message}. Never resent; act in TWS.")
    assert restarted.placed == [] and restarted.cancelled == []


def test_fix1_the_1555_finish_reports_an_open_release_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, clock, _, _, stop_id = _filled_manual_buy(tmp_path, monkeypatch, "0000e0d5.000001a9.01.01")
    close_id = queue_manual("/close AAPL", message_id=2, now=clock.value, owner_shares=10)
    clock.advance(1)
    bridge.tick()
    assert close_id in bridge.manual.releases
    clock.value = ny(15, 55)
    capsys.readouterr()

    assert bridge.finish() == pb.EXIT_OK  # finish follows owner rows, never their releases
    assert unsold_page(close_id, "CLOSE 10 AAPL MKT", stop_id, "is being cancelled (IBKR has not confirmed yet)",
                       "the session ended at 15:55 ET before IBKR confirmed the cancel") in critical(events(capsys))
    assert manual_row(close_id).status == "error" and bridge.manual.releases == {}


# MEDIUM-1 (b): the stop fit never cancels on a position update that has not arrived.

def test_fix1_the_stop_fit_never_cancels_a_stop_on_an_unconfirmed_zero_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18 probe P8: e63a599 cancelled the stop 15 s after the fill because the position still read 0.
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch, "/buy 10 AAPL stop 185")
    fill(broker, parent_id, 10, 190.01, f"manual:{buy_id}", "0000e0d5.000001b1.01.01")
    clock.advance(2)
    bridge.tick()
    assert manual_row(buy_id).status == "filled"
    capsys.readouterr()
    for _ in range(16):  # 32 s: the position update for the fill has not arrived
        clock.advance(2)
        bridge.tick()

    assert broker.cancelled == [] and [order_id for order_id, _ in broker.placed if order_id == stop_id] == [stop_id]
    lags = [text for text in critical(events(capsys)) if "no position update" in text]
    assert len(lags) == 1 and re.fullmatch(
        rf"AAPL: no position update 1\ds after your buy's fill \(order {parent_id}\) — its attached stop {stop_id} is"
        r" kept as it is until the position shows the shares; check TWS", lags[0])

    push_position(broker, AAPL_ID, 10)  # the position catches up: the stop fits the 10 shares already
    for _ in range(3):
        clock.advance(2)
        bridge.tick()
    assert broker.cancelled == [] and stop_id not in bridge.manual.stop_fits
    assert bridge.account_open_risk().unprotected == ()


def test_fix1_the_stop_fit_reduces_only_once_the_position_shows_the_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch)  # 10 @ 185 stop 180
    broker.execDetails(-1, None, execution("0000e0d5.000001b2.01.01", parent_id, 4, 185.0, acctNumber=ACCOUNT,
                                           orderRef=f"manual:{buy_id}", side="BOT", time="20261013  10:00:30"))
    broker.orderStatus(parent_id, "Cancelled", 4, 6, 185.0)  # 4 bought, the rest cancelled; no position update yet
    broker.placed.clear()
    for _ in range(10):  # 20 s, past the 15 s grace
        clock.advance(2)
        bridge.tick()
    assert broker.placed == [] and broker.cancelled == []  # the position reads 0: never fitted to it

    push_position(broker, AAPL_ID, 4)
    clock.advance(2)
    bridge.tick()
    [(order_id, fitted)] = broker.placed
    assert (order_id, fitted.totalQuantity) == (stop_id, 4)


# MEDIUM-2: a book refresh is complete only once every open-orders request up to it is answered.

class LaggingBook(FakeBroker):
    """N18 probe P3: TWS answers reqAllOpenOrders from its own thread: A after 0.3 s, then B and openOrderEnd."""

    def reqAllOpenOrders(self) -> None:
        book = list(self.book)

        def answer() -> None:
            time.sleep(0.3)
            order_id, contract, item, status, _ = book[0]
            self.openOrder(order_id, contract, item, SimpleNamespace(status=status))
            time.sleep(0.4)
            order_id, contract, item, status, _ = book[1]
            self.openOrder(order_id, contract, item, SimpleNamespace(status=status))
            self.openOrderEnd()

        threading.Thread(target=answer, daemon=True).start()


def _book_with_owner_stop(broker: FakeBroker) -> None:
    owner_stop = stop_leg(500, 100, 180.0, parent=0, ref="", client=0)  # the owner's TWS stop on all 100 shares
    other = pb.order("BUY", "LMT", 1, ACCOUNT, "", lmtPrice=10.0)
    other.clientId, other.permId = 0, 770501
    broker.book = [(500, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), owner_stop, "PreSubmitted", 100.0),
                   (501, SimpleNamespace(conId=TSLA_ID, symbol="TSLA"), other, "Submitted", 1.0)]


def test_fix1_a_timed_out_refresh_never_lets_the_next_sell_see_a_partial_book(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    sell_id = queue_manual("/sell 50 AAPL", message_id=1, owner_shares=100)
    broker = LaggingBook(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    broker.connect("127.0.0.1", 7497, CLIENT_ID)
    broker.reqAccountSummary(1, "All", "NetLiquidation")
    broker.reqPositions()
    _book_with_owner_stop(broker)
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.sync_timeout = 2.0
    assert bridge.refresh_order_book(timeout=0.5) is None  # the watch's refresh times out (3 s in production)
    bridge.manual.tick()  # the owner's sell, while that answer is still on its way

    # e63a599 took the late answer's end for its own and saw only TSLA: SELL 50 on 100 held under a 100-sh stop.
    assert [item for _, item in broker.placed if item.action == "SELL"] == []
    # Changed by v7c fix 4 (N18d-LOW-1; fix 3 refused it open_orders_unavailable at once): the desk waits, in
    # its own budget, for the owed answer, then sends its own request and reads the whole book.
    assert sorted(item.order_id for item in bridge.order_book) == [500, 501]
    assert (manual_row(sell_id).status, manual_row(sell_id).reject_code) == ("rejected", "insufficient_shares")


class HeldAnswers(FakeBroker):
    """TWS answers reqAllOpenOrders in request order, when the test says so (``hold``); each answer is the
    book at request time. ``on_request`` runs right after a request, while the bridge waits for it."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.hold = False
        self.owed: list[list[tuple[int, Any, Any, str, float]]] = []
        self.requests = 0
        self.on_request: Callable[[int], None] | None = None

    def reqAllOpenOrders(self) -> None:
        self.requests += 1
        if not self.hold:
            super().reqAllOpenOrders()
        else:
            self.owed.append(list(self.book))
        if self.on_request is not None:
            self.on_request(self.requests)

    def deliver(self, orders: int | None = None) -> None:
        """The oldest answer still owed: its next ``orders`` orders, or the rest of it and its openOrderEnd."""
        answer = self.owed[0]
        sent = answer if orders is None else answer[:orders]
        for order_id, contract, item, status, remaining in sent:
            self.openOrder(order_id, contract, item, SimpleNamespace(status=status))
            self.orderStatus(order_id, status, 0, remaining, 0, item.permId, 0, 0, item.clientId, "", 0)
        del answer[:len(sent)]
        if orders is None:
            self.owed.pop(0)
            self.openOrderEnd()


def held_answers(**kwargs: Any) -> HeldAnswers:
    broker = HeldAnswers(**kwargs)
    broker.connect("127.0.0.1", 7497, CLIENT_ID)
    broker.reqAccountSummary(1, "All", "NetLiquidation")
    broker.reqPositions()
    return broker


def test_fix1_a_late_answer_never_lets_a_sell_through_while_its_request_is_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    sell_id = queue_manual("/sell 50 AAPL", message_id=1, owner_shares=100)
    broker = held_answers(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    _book_with_owner_stop(broker)
    broker.hold = True
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    assert bridge.refresh_order_book(timeout=0.01) is None  # request 1: no answer in time
    broker.deliver(1)  # the AAPL stop of answer 1 arrives late, before the sell's own book read
    broker.on_request = lambda number: broker.deliver() if number == 2 else None  # (fix 1's straddle; never called)
    bridge.manual.tick()

    assert [item for _, item in broker.placed if item.action == "SELL"] == []
    assert (manual_row(sell_id).status, manual_row(sell_id).reject_code) == ("rejected", "open_orders_unavailable")
    assert broker.requests == 1  # v7c fix 3 (N18c-LOW-1): no second request while the first is owed


def test_fix1_an_open_orders_end_nobody_asked_for_never_completes_a_later_request(tmp_path: Path) -> None:
    broker = held_answers(positions={AAPL_ID: 100}, contracts=STOCK)
    _book_with_owner_stop(broker)
    bridge = make_bridge(tmp_path, broker)
    broker.hold = True
    broker.openOrderEnd()  # TWS's own end, e.g. after the open orders it reports at connect
    broker.openOrderEnd()
    assert bridge.refresh_order_book(timeout=0.01) is None  # never completed by an end nobody asked for
    broker.deliver()
    assert bridge.refresh_order_book(timeout=0.01) is None  # request 2's answer is owed, not banked
    broker.deliver()
    broker.openOrderEnd()  # a stray end after every request is answered: dropped as well
    assert bridge.refresh_order_book(timeout=0.01) is None
    assert (broker.open_orders_requested, broker.open_orders_answered) == (3, 2)
    broker.deliver()
    broker.hold = False
    assert sorted(item.order_id for item in bridge.refresh_order_book(timeout=0.01) or ()) == [500, 501]


def test_fix1_the_book_recovers_once_the_slow_answer_arrives_and_shows_the_current_orders(tmp_path: Path) -> None:
    # Rewritten by v7c fix 3 (N18c-LOW-1): fix 1 sent request 2 while answer 1 was owed and dropped answer 1's
    # orders at its end; now nothing is sent while a request is owed, and the next request reads afresh.
    broker = held_answers(positions={AAPL_ID: 100}, contracts=STOCK)
    _book_with_owner_stop(broker)
    bridge = make_bridge(tmp_path, broker)
    broker.hold = True
    assert bridge.refresh_order_book(timeout=0.01) is None  # answer 1 (AAPL stop and TSLA) is owed
    del broker.book[0]  # the owner cancels the AAPL stop in TWS; TSLA's order stays
    assert bridge.refresh_order_book(timeout=0.01) is None and broker.requests == 1  # nothing sent while owed

    broker.deliver()  # the slow answer 1 arrives at last, still with the AAPL stop
    broker.hold = False
    book = bridge.refresh_order_book(timeout=0.01)  # the next refresh is sent and completes
    assert book is not None and [item.order_id for item in book] == [501] and broker.requests == 2


# MEDIUM-3: one behaviour test per fail-closed branch of the coverage rule and of the reservation.

def _covered_world(tmp_path: Path, *, held: int = 100, own: dict[int, tuple[Any, str]] | None = None,
                   book: tuple[tuple[int, Any, str, float], ...] = (),
                   own_in_book: bool = False) -> tuple[Any, FakeBroker]:
    """``held`` AAPL at avgCost 50 (TWS-bought); ``own``: this client's (client 18) non-leg orders by id with
    their status, reported to reqOpenOrders; ``book``: every other client's orders. The book is fresh."""
    own = own or {}
    broker = connected_broker(positions={AAPL_ID: held}, contracts=STOCK,
                              live_orders={order_id: status for order_id, (_, status) in own.items()},
                              order_objects={order_id: item for order_id, (item, _) in own.items()})
    for order_id in own:
        broker.open_order_contracts[order_id] = SimpleNamespace(conId=AAPL_ID, symbol="AAPL")
    broker.own_in_all_open_orders = own_in_book
    broker.reqOpenOrders()
    for order_id, item, status, remaining in book:
        broker.book.append((order_id, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), item, status, remaining))
    bridge = make_bridge(tmp_path, broker)
    assert bridge.refresh_order_book() is not None
    return bridge, broker


def tws_stop(order_id: int, quantity: int, price: float = 45.0, **fields: Any) -> Any:
    item = stop_leg(order_id, quantity, price, parent=0, ref="", client=0)  # placed by hand in TWS (client 0)
    for name, value in fields.items():
        setattr(item, name, value)
    return item


@pytest.mark.parametrize(("status", "unprotected"), [
    ("PreSubmitted", ()), ("Cancelled", (("AAPL", "100 sh held, working stops cover 0"),))])
def test_fix1_n01_an_own_stop_covers_only_while_it_is_live(tmp_path: Path, status: str, unprotected: Any) -> None:
    own = stop_leg(50, 100, 45.0, parent=0, ref="manual:ownstop1")  # an owner /stop the bridge sent
    bridge, _ = _covered_world(tmp_path, own={50: (own, status)})
    risk = bridge.account_open_risk()
    assert risk.unprotected == unprotected
    assert risk.total == (Decimal("500") if not unprotected else 0)  # 100 x (50 - 45)


@pytest.mark.parametrize(("order_type", "covers"), [("STP", True), ("STP LMT", False), ("TRAIL", False)])
def test_fix1_n02_only_a_plain_stop_in_the_book_covers(tmp_path: Path, order_type: str, covers: bool) -> None:
    bridge, _ = _covered_world(tmp_path, book=((5, tws_stop(5, 100, orderType=order_type), "PreSubmitted", 100.0),))
    expected = () if covers else (("AAPL", "100 sh held, working stops cover 0"),)
    assert bridge.account_open_risk().unprotected == expected  # a stop limit or a trail may never fill


@pytest.mark.parametrize(("account", "covers"), [(ACCOUNT, True), ("", True), ("OTHER-ACCOUNT", False)])
def test_fix1_n03_another_accounts_stop_covers_nothing(tmp_path: Path, account: str, covers: bool) -> None:
    bridge, _ = _covered_world(tmp_path, book=((5, tws_stop(5, 100, account=account), "PreSubmitted", 100.0),))
    expected = () if covers else (("AAPL", "100 sh held, working stops cover 0"),)
    assert bridge.account_open_risk().unprotected == expected


def test_fix1_n04_an_own_stop_also_in_the_all_clients_book_covers_once(tmp_path: Path) -> None:
    own = stop_leg(50, 100, 45.0, parent=0, ref="manual:ownstop2")
    bridge, _ = _covered_world(tmp_path, held=200, own={50: (own, "PreSubmitted")}, own_in_book=True)
    assert [(item.client_id, item.order_id) for item in bridge.order_book] == [(CLIENT_ID, 50)]  # in the book too
    assert bridge.account_open_risk().unprotected == (("AAPL", "200 sh held, working stops cover 100"),)


def test_fix1_n05_a_short_position_is_unprotected(tmp_path: Path) -> None:
    bridge, _ = _covered_world(tmp_path, held=-50)
    assert bridge.account_open_risk().unprotected == (("AAPL", "short 50 sh"),)
    assert bridge.budget_problem(Decimal("50")).startswith("v7c_unprotected_position: AAPL short 50 sh")


@pytest.mark.parametrize("avg_cost", [0.0, sys.float_info.max])  # 9.81 sends UNSET as 1.8e308
def test_fix1_n06_shares_at_an_unknown_average_cost_are_unprotected(tmp_path: Path, avg_cost: float) -> None:
    bridge, broker = _covered_world(tmp_path, book=((5, tws_stop(5, 100), "PreSubmitted", 100.0),))
    assert bridge.account_open_risk().unprotected == ()
    broker.position(ACCOUNT, STOCK[AAPL_ID], 100.0, avg_cost)
    assert bridge.account_open_risk().unprotected == (("AAPL", "100 sh held at an unknown average cost"),)


def test_fix1_n08_stops_one_share_short_leave_the_position_unprotected(tmp_path: Path) -> None:
    bridge, _ = _covered_world(tmp_path, book=((5, tws_stop(5, 60), "PreSubmitted", 60.0),
                                               (6, tws_stop(6, 39, 44.0), "PreSubmitted", 39.0)))
    assert bridge.account_open_risk().unprotected == (("AAPL", "100 sh held, working stops cover 99"),)


def test_fix1_n09_a_partly_filled_stop_covers_only_what_it_has_left(tmp_path: Path) -> None:
    own = stop_leg(50, 150, 45.0, parent=0, ref="manual:ownstop3")  # 150 sh, of which 60 already sold
    bridge, broker = _covered_world(tmp_path, own={50: (own, "Submitted")})
    broker.orderStatus(50, "Submitted", 60, 90, 44.9)
    assert bridge.account_open_risk().unprotected == (("AAPL", "100 sh held, working stops cover 90"),)


def test_fix1_n13_an_owner_buy_execution_is_never_a_realized_gain(tmp_path: Path) -> None:
    broker = connected_broker()
    bridge = make_bridge(tmp_path, broker)
    broker.position(ACCOUNT, STOCK[AAPL_ID], 100.0, 200.0)  # cost basis 200
    broker.execDetails(-1, STOCK[AAPL_ID], execution("0000e0d5.000001c1.01.01", 9, 100, 190.0, acctNumber=ACCOUNT,
                                                       orderRef="manual:sold", side="SLD", time="20261013  10:00:30"))
    broker.execDetails(-1, STOCK[AAPL_ID], execution("0000e0d5.000001c2.01.01", 10, 100, 210.0, acctNumber=ACCOUNT,
                                                       orderRef="manual:added", side="BOT", time="20261013  10:01:00"))
    assert bridge.computed_realized() == (Decimal("-1000"), 0)  # the buy above the basis realizes nothing
    assert bridge.account_realized_loss() == Decimal("1000")


def test_fix1_n21_an_owner_buy_ibkr_has_not_reported_yet_is_reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    queue_manual("/buy 10 AAPL 185 stop 180", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    broker.delay_echoes = True  # TWS's openOrder and orderStatus for the new orders come later
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.tick()
    [(parent_id, _), _] = broker.placed

    assert broker.order_status.get(parent_id) is None  # placed, not reported by IBKR yet
    assert bridge.account_open_risk().total == Decimal("50.00")  # reserved all the same: 10 x (185 - 180)
    broker.deliver_echoes()
    assert bridge.account_open_risk().total == Decimal("50.00")


# LOW-1 .. LOW-5, LOW-8

def test_fix1_low1_a_filled_owner_buy_stays_in_the_budget_until_its_position_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # N18 probe P2: e63a599 read 50.10 (working) -> 0 (filled, position not shown yet) -> 50.10 (shown).
    bridge, broker, clock, buy_id, parent_id, _ = manual_buy_world(tmp_path, monkeypatch, "/buy 10 AAPL stop 185")
    totals = [bridge.account_open_risk().total]
    fill(broker, parent_id, 10, 190.01, f"manual:{buy_id}", "0000e0d5.000001d1.01.01")
    for _ in range(3):
        clock.advance(2)
        bridge.tick()
        totals.append(bridge.account_open_risk().total)
    assert manual_row(buy_id).status == "filled" and buy_id in bridge.manual.settling
    broker.position(ACCOUNT, STOCK[AAPL_ID], 10.0, 190.01)
    totals.append(bridge.account_open_risk().total)  # shown: section 3 counts it, the reservation ends
    clock.advance(2)
    bridge.tick()
    totals.append(bridge.account_open_risk().total)

    assert totals == [Decimal("50.10")] * 6  # never 0: 10 x (190.01 - 185)
    assert bridge.manual.settling == {}


def test_fix1_low2_a_quiet_reqpnl_loss_still_blocks_after_120s(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18 probe P4: e63a599 dropped IBKR's -14,000 (2.1 %) once it was 121 s old and sent a MOMO entry.
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, clock=clock)
    broker.pnl(pb.PNL_REQUEST_ID, -14000.0, 0.0, -14000.0)  # e.g. carried shares stopped out: 2.1 % of NLV
    assert bridge.budget_problem(Decimal("50")).startswith(pb.DAILY_LOSS_LIMIT)
    with broker.lock:
        broker.pnl_at = time.monotonic() - 121  # a flat account: no P&L change, no further pnl() callback
    capsys.readouterr()
    assert bridge.budget_problem(Decimal("50")).startswith(pb.DAILY_LOSS_LIMIT)
    bridge.decisions = lambda: [decision_row("d1", "MSFT", created=clock.value)]
    bridge.tick()

    assert v7c_skip(bridge, "d1").startswith("daily_loss_limit: daily loss limit reached (account realized loss"
                                             " 14000.00")
    assert broker.placed == []
    out = [text for _, text in events(capsys)]
    assert "v7c_pnl_source=computed" in out  # the source line stays: IBKR's value is no longer fresh
    assert [line for line in out if line.startswith("v7c_pnl_stale")] == [
        "v7c_pnl_stale: IBKR's last realized -14000.00 is 121s old — it stays a lower bound for today's loss"]


def test_fix1_low3_unfilled_bot_shares_never_hide_owner_shares(tmp_path: Path) -> None:
    # N18 probe P5: 40 of 100 bot shares bought, the owner buys 100 more in TWS under his own stop at 40.
    item = bracket("b", "BOT", status="open", parent=40, filled=40, qty=100)
    broker = connected_broker(executions=(execution("p", 40, 40, 50.0, acctNumber=ACCOUNT,
                                                    orderRef="autotrader:MOMO_HOD_V2:b", time="20261013  10:00:01"),),
                              live_orders={40: "Submitted", 41: "Submitted", 42: "PreSubmitted"},
                              order_objects={42: stop_leg(42, 100, 49.5, parent=40, ref="autotrader:MOMO_HOD_V2:b")},
                              positions={400: 140})
    broker.reqOpenOrders()
    broker.reqExecutions(1, SimpleNamespace(clientId=CLIENT_ID))
    broker.position(ACCOUNT, SimpleNamespace(conId=400, symbol="BOT", secType="STK", currency="USD"), 140.0, 50.0)
    broker.book.append((600, SimpleNamespace(conId=400, symbol="BOT"), tws_stop(600, 100, 40.0), "PreSubmitted", 100.0))
    bridge = make_bridge(tmp_path, broker, brackets={"b": item})
    bridge.refresh_order_book()

    risk = bridge.account_open_risk()
    assert "BOT account 100 held @ 50.0, stop 40.0: 1000.00" in risk.lines  # e63a599: 40 held, 400.00
    assert risk.total == Decimal("1050.00")  # + the bracket: 40 held and 60 unfilled x (50.00 - 49.50)


def test_fix1_low4_a_second_unprotected_episode_pages_again(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # N18 probe P6: e63a599 paged the first episode only.
    clock = Clock(DECIDED)
    broker = connected_broker(positions={AAPL_ID: 100}, contracts=STOCK)
    bridge = make_bridge(tmp_path, broker, clock=clock)
    capsys.readouterr()
    for _ in range(10):
        bridge.tick()
        clock.advance(2)
    page = ("v7c_unprotected_position AAPL: 100 sh held, working stops cover 0 — every risk-increasing order (bot and"
            f" manual) is refused; {V7C_ADVICE_FOR_AAPL}")
    assert critical(events(capsys)) == [page]
    broker.book.append((0, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), tws_stop(0, 100), "PreSubmitted", 100.0))
    clock.advance(31)
    bridge.tick()
    assert not bridge.account_open_risk().unprotected  # protected: the episode is over
    broker.book.clear()  # the owner cancels the stop later in the day
    for _ in range(25):
        clock.advance(2)
        bridge.tick()

    assert critical(events(capsys)) == [page]  # the second episode pages again, once


def test_fix1_low5_a_needs_attention_bracket_follows_the_account_once_the_grace_ends(tmp_path: Path) -> None:
    item = bracket("att", "ATT", status="needs_attention", parent=30, filled=100,
                   attention_reason="not found at IBKR at startup")  # its stop leg is gone: O3 blocks every entry
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(positions={300: 100})
    bridge = make_bridge(tmp_path, broker, brackets={"att": item}, clock=clock)
    why = (("ATT", "100 sh of the MOMO_HOD_V2 bracket, whose stop order 32 is not live"),)
    assert bridge.account_open_risk().unprotected == why
    broker.position(ACCOUNT, SimpleNamespace(conId=300, symbol="ATT"), 0.0, 0.0)  # the owner closed it in TWS
    assert bridge.account_open_risk().unprotected == why  # a position update may trail a fill: the grace first
    clock.advance(10)
    assert bridge.account_open_risk().unprotected == why
    clock.advance(6)
    risk = bridge.account_open_risk()
    assert risk.unprotected == () and risk.total == 0  # O3's block lifts
    assert bridge.budget_problem(Decimal("50")) is None


def test_fix1_low8_an_oversized_stop_warns_and_pages_but_never_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18 probe P7b: the 10-15 GTC legs: IP 2,941 sh, VEEV 320 sh on 100 VEEV held.
    ip, veev = 9001, 9002
    carried = {ip: SimpleNamespace(conId=ip, symbol="IP", secType="STK", currency="USD", primaryExchange="NYSE"),
               veev: SimpleNamespace(conId=veev, symbol="VEEV", secType="STK", currency="USD", primaryExchange="NYSE")}
    legs = {22: stop_leg(22, 2941, 33.53, parent=20, ref="autotrader:MOMO_HOD_V2:ip1015"),
            28: stop_leg(28, 320, 308.49, parent=26, ref="autotrader:MOMO_HOD_V2:veev1015")}
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(bid=Decimal("50.10"), positions={ip: 2941, veev: 100}, contracts=carried,
                              live_orders={22: "PreSubmitted", 28: "PreSubmitted"}, order_objects=legs)
    broker.open_order_contracts.update({22: carried[ip], 28: carried[veev]})
    bridge = make_bridge(tmp_path, broker, clock=clock)
    bridge.reconcile()
    broker.position(ACCOUNT, carried[ip], 2941.0, 33.94)
    broker.position(ACCOUNT, carried[veev], 100.0, 311.14)
    bridge.decisions = lambda: [decision_row("d1", "MSFT", created=clock.value)]
    capsys.readouterr()
    bridge.tick()
    clock.advance(16)  # v7c fix 4 (N18d-LOW-2): the WARN and the page wait for the 15 s position grace
    bridge.tick()

    assert bridge.brackets["d1"].status == "sent"  # owner Q6: never a block
    assert bridge.account_open_risk().oversized == (("VEEV", Decimal("320"), Decimal("100"), Decimal("100")),)
    out = events(capsys)
    assert [text for kind, text in out if kind == "INFO" and "v7c_stop_oversized" in text] == [
        "WARN v7c_stop_oversized VEEV: working stops sell 320 sh but cover 100 held — counted as covering; entries"
        " are not blocked"]  # once per contract and quantity
    assert [text for text in critical(out) if "v7c_stop_oversized" in text] == [
        "v7c_stop_oversized VEEV: working stops sell 320 sh but cover 100 held: if they all fire the account is short"
        " 220 sh — resize or cancel the extra stop in TWS"]


# LOW-9: further test gaps (N14, N15, N17, N19, N22; N39 is in test_deploy_apply.py).

def test_fix1_n14_an_owner_buy_reads_the_book_afresh_before_its_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), contracts=STOCK)
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()  # the watch reads the book: nothing held, no stop
    # Since then the owner bought 100 TSLA in TWS under a GTC stop placed there too.
    push_position(broker, TSLA_ID, 100)
    broker.book.append((7, SimpleNamespace(conId=TSLA_ID, symbol="TSLA"), tws_stop(7, 100, 40.0), "PreSubmitted",
                        100.0))
    row_id = queue_manual("/buy 10 AAPL stop 185", message_id=1, now=clock.value)
    clock.advance(4)  # the watch's book is 4 s old: neither its cadence nor its unprotected re-read is due
    bridge.tick()

    assert manual_row(row_id).status == "sent"  # the buy's own book read saw the TWS stop


def test_fix1_n15_a_plain_stop_is_never_an_attached_stop_to_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, broker, clock, _, parent_id, stop_id = _filled_manual_buy(tmp_path, monkeypatch, "0000e0d5.000001e1.01.01")
    stop_row = queue_manual("/stop 10 AAPL at 180", message_id=2, now=clock.value, owner_shares=10)
    push_position(broker, AAPL_ID, 20)  # 10 more bought in TWS: the /stop may cover them
    clock.advance(1)
    bridge.tick()
    [(plain_id, plain)] = [(order_id, item) for order_id, item in broker.placed
                           if item.orderRef == f"manual:{stop_row}"]
    assert (plain.orderType, plain.parentId) == ("STP", 0)

    # Only a stop with a parent is a buy's attached stop: /close, /sell all and /closeall never cancel a /stop.
    assert bridge.manual.own_entry_stops(AAPL_ID) == [(stop_id, parent_id)]
    assert bridge.manual.own_entry_stops(None) == [(stop_id, parent_id)]
    with broker.lock:
        assert not bridge.manual.is_entry_stop(broker.order_objects[plain_id])


def test_fix1_n17_a_closeall_whose_stop_cancel_is_unconfirmed_still_closes_everything_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, clock, _, _, stop_id = _filled_manual_buy(tmp_path, monkeypatch, "0000e0d5.000001e2.01.01")
    push_position(broker, TSLA_ID, 50)  # also 50 TSLA, held without any stop
    close_id = queue_manual("/closeall", message_id=2, now=clock.value)
    broker.placed.clear()
    clock.advance(1)
    bridge.tick()
    assert broker.cancelled == [stop_id] and broker.placed == []
    capsys.readouterr()
    clock.advance(11)  # IBKR never reports the AAPL stop cancelled
    bridge.tick()

    [(child, sell)] = broker.placed
    assert (sell.action, sell.totalQuantity) == ("SELL", 50) and bridge.manual.tracked[close_id].con_ids == {
        child: TSLA_ID}
    pages = critical(events(capsys))
    assert pages[0] == (f"manual order #{close_id[:6]}: IBKR did not confirm the cancel of your attached stop(s)"
                        f" [{stop_id}] in 10s — closing everything else; check TWS")
    assert pages[1] == unsold_page(close_id, "CLOSE_ALL", stop_id, "is being cancelled (IBKR has not confirmed yet)",
                                   "AAPL: 10 of 10 reserved by working sells/stops — cancel them first — not sold")
    assert len(pages) == 2


def test_fix1_n19_a_stop_placed_since_the_last_book_never_pages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = connected_broker(contracts=STOCK)
    bridge = make_bridge(tmp_path, broker, clock=clock)
    bridge.tick()  # the book is read: nothing held
    clock.advance(1)
    push_position(broker, AAPL_ID, 100)  # bought in TWS, with its stop, a second later
    broker.book.append((5, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), tws_stop(5, 100), "PreSubmitted", 100.0))
    capsys.readouterr()
    for _ in range(12):  # 24 s, all inside the 30 s cadence
        clock.advance(2)
        bridge.tick()

    assert not critical(events(capsys))  # re-read before paging: the stop covers it
    assert bridge.account_open_risk().unprotected == ()


def test_fix1_n22_the_post_submit_invariant_reads_the_stored_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, decisions=(decision_row("d1", "AAPL"),))
    original = broker.placeOrder

    def place(order_id: int, contract: Any, sent: Any) -> None:
        if sent.orderType == "STP":  # IBKR shows the stop a dollar lower than the intent the budget checked
            sent = copy.copy(sent)
            sent.auxPrice -= 1.0
        original(order_id, contract, sent)

    broker.placeOrder = place
    capsys.readouterr()
    bridge.tick()

    assert bridge.brackets["d1"].status == "sent"
    assert ("CRITICAL", "v7c_budget_invariant_broken: open risk grew by 150.00 with AAPL (MOMO_HOD_V2), more than"
            " its own 50.00 (0.00 -> 150.00) — check TWS") in events(capsys)


# ---- v7c fix 2: review N18b (LOW-1..4) ----------------------------------------------------------------------------


def oversized_pages(out: list[tuple[str, str]]) -> list[str]:
    return [text for text in critical(out) if "v7c_stop_oversized" in text]


def test_fix2_low1_no_oversized_page_while_a_partly_filled_buy_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18b probe P15: fix 1 paged "short 6 sh — resize or cancel the extra stop" on the buy's own stop.
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch)  # 10 @ 185 stop 180
    broker.execDetails(-1, None, execution("0000e0d5.000002a1.01.01", parent_id, 4, 185.0, acctNumber=ACCOUNT,
                                           orderRef=f"manual:{buy_id}", side="BOT", time="20261013  10:00:30"))
    broker.orderStatus(parent_id, "Submitted", 4, 6, 185.0)  # 4 of 10 bought; the buy still works for 6
    push_position(broker, AAPL_ID, 4)
    capsys.readouterr()
    for _ in range(3):
        clock.advance(2)
        bridge.tick()

    assert bridge.account_open_risk().oversized == () and oversized_pages(events(capsys)) == []
    assert broker.cancelled == [] and stop_id not in [order_id for order_id, _ in broker.placed[2:]]


def test_fix2_low1_no_oversized_page_for_an_unfilled_buy_next_to_held_shares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18b probe P15b: 100 held under the owner's TWS stop, an unfilled /buy 10 whose stop cannot fire yet.
    ledger = manual_ledger(tmp_path, monkeypatch)
    queue_manual("/buy 10 AAPL 185 stop 180", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    broker.position(ACCOUNT, STOCK[AAPL_ID], 100.0, 190.0)
    broker.book.append((7, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), tws_stop(7, 100, 181.0), "PreSubmitted",
                        100.0))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    capsys.readouterr()
    for _ in range(3):
        bridge.tick()
        clock.advance(2)

    assert [sent.action for _, sent in broker.placed] == ["BUY", "SELL"]
    assert bridge.account_open_risk().oversized == () and oversized_pages(events(capsys)) == []


def test_fix2_low1_a_buy_that_still_works_never_makes_its_stop_oversized_even_without_the_desk(
    tmp_path: Path
) -> None:
    # The owner desk is off for this run (its startup failed), yet a /buy placed earlier still works at IBKR.
    contract = SimpleNamespace(conId=AAPL_ID, symbol="AAPL")
    parent = pb.order("BUY", "LMT", 10, ACCOUNT, "manual:earlier", lmtPrice=185.0, tif="DAY")
    attached = stop_leg(51, 10, 180.0, parent=50, ref="manual:earlier")
    broker = connected_broker(positions={AAPL_ID: 4}, contracts=STOCK, live_orders={50: "Submitted", 51: "PreSubmitted"},
                              order_objects={50: parent, 51: attached})
    broker.open_order_contracts.update({50: contract, 51: contract})
    broker.reqOpenOrders()
    broker.position(ACCOUNT, STOCK[AAPL_ID], 4.0, 185.0)  # 4 of the 10 bought so far
    bridge = make_bridge(tmp_path, broker)  # no ManualDesk attached
    assert bridge.refresh_order_book() is not None

    assert bridge.account_open_risk().oversized == ()  # its stop is sized to the buy, which may fill 6 more
    broker.orderStatus(50, "Cancelled", 4, 6, 185.0)  # the buy dies: the 10-share stop now oversells 4 held
    assert bridge.account_open_risk().oversized == (("AAPL", Decimal("10"), Decimal("4"), Decimal("4")),)


def test_fix2_low1_a_dead_buys_stop_pages_only_once_the_desk_has_fitted_it_and_it_still_oversells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch)  # 10 @ 185 stop 180
    broker.execDetails(-1, None, execution("0000e0d5.000002a2.01.01", parent_id, 4, 185.0, acctNumber=ACCOUNT,
                                           orderRef=f"manual:{buy_id}", side="BOT", time="20261013  10:00:30"))
    push_position(broker, AAPL_ID, 4)
    broker.orderStatus(parent_id, "Cancelled", 4, 6, 185.0)  # the rest of the buy is cancelled
    capsys.readouterr()
    for _ in range(12):  # the fit's grace, then the fit and TWS's echo of it
        clock.advance(2)
        bridge.tick()
    assert [(order_id, sent.totalQuantity) for order_id, sent in broker.placed[2:]] == [(stop_id, 4)]
    assert oversized_pages(events(capsys)) == []  # never paged while the desk sized the stop itself

    push_position(broker, AAPL_ID, 2)  # later the owner sells 2 in TWS: the 4-share stop now oversells
    for _ in range(9):  # v7c fix 4 (N18d-LOW-2): the page waits for the 15 s position grace (was 2 ticks)
        clock.advance(2)
        bridge.tick()
    assert oversized_pages(events(capsys)) == [
        "v7c_stop_oversized AAPL: working stops sell 4 sh but cover 2 held: if they all fire the account is short"
        " 2 sh — resize or cancel the extra stop in TWS"]


def test_fix2_low2_an_unanswered_book_request_pages_once_after_60s_and_again_in_a_later_episode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock(DECIDED + timedelta(seconds=5))
    broker = held_answers(contracts=STOCK)
    bridge = make_bridge(tmp_path, broker, clock=clock)
    bridge.sync_timeout = 0.001
    broker.hold = True  # TWS does not answer request 1 (and, in order, nothing after it)
    capsys.readouterr()
    pages: list[list[str]] = []
    for _ in range(13):  # 0..120 s
        bridge.tick()
        pages.append([text for text in critical(events(capsys)) if "v7c_order_book_unavailable" in text])
        clock.advance(10)
    page = ("v7c_order_book_unavailable: TWS has not answered an open-orders request for 60s — the order book is"
            " unavailable: Telegram sells, /close and /closeall are refused, and stops placed in TWS no longer count"
            " for the day risk budget, so a position they protect blocks every entry; check TWS and restart the"
            " robot")
    assert pages[:6] == [[]] * 6 and pages[6] == [page] and pages[7:] == [[]] * 6  # at 60 s, once

    while broker.owed:  # TWS answers the backlog: the episode ends
        broker.deliver()
    broker.hold = False
    bridge.tick()
    assert ("INFO", "v7c_order_book_unavailable: TWS answered every open-orders request again — the order book is"
            " back") in events(capsys)
    assert bridge.book_fresh()

    broker.hold = True  # a later episode pages again
    for _ in range(9):
        clock.advance(10)
        bridge.tick()
    assert len([text for text in critical(events(capsys)) if "v7c_order_book_unavailable" in text]) == 1


def test_fix2_low3_a_position_reported_before_the_execution_fits_the_stop_with_no_double_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18b probe P14: fix 1 kept the 4 shares unshown forever: counted twice, the stop never fitted.
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch)  # 10 @ 185 stop 180
    broker.position(ACCOUNT, STOCK[AAPL_ID], 4.0, 185.0)  # TWS shows the 4 shares first ...
    broker.account_positions[AAPL_ID] = 4
    broker.execDetails(-1, None, execution("0000e0d5.000002b1.01.01", parent_id, 4, 185.0, acctNumber=ACCOUNT,
                                           orderRef=f"manual:{buy_id}", side="BOT", time="20261013  10:00:30"))
    broker.orderStatus(parent_id, "Cancelled", 4, 6, 185.0)  # ... then the execution; the rest is cancelled
    assert bridge.manual.unshown_fills({parent_id}, AAPL_ID, f"manual:{buy_id}") == 0
    assert bridge.account_open_risk().lines == ("AAPL account 4 held @ 185.0, stop 180.0: 20.00",)  # counted once
    capsys.readouterr()
    for _ in range(12):
        clock.advance(2)
        bridge.tick()

    assert broker.cancelled == [] and [(order_id, sent.totalQuantity) for order_id, sent in broker.placed[2:]] == [
        (stop_id, 4)]  # fitted to the 4 shares the buy holds
    assert not critical(events(capsys))
    assert bridge.account_open_risk().total == Decimal("20.00") and bridge.manual.settling == {}


def test_fix2_low3_a_report_that_does_not_hold_the_fills_on_top_of_earlier_ones_never_shows_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    first = queue_manual("/buy 10 AAPL 185 stop 180", message_id=1)
    second = queue_manual("/buy 10 AAPL 186 stop 180", message_id=2)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), contracts=STOCK)
    broker.own_in_all_open_orders = True
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(first_buy, _), _] = broker.placed
    fill(broker, first_buy, 10, 185.0, f"manual:{first}", "0000e0d5.000002b2.01.01")  # its position update lags
    clock.advance(1)
    bridge.tick()  # the first buy is filled and settling; the second goes out
    [_, _, (second_buy, _), _] = broker.placed
    broker.position(ACCOUNT, STOCK[AAPL_ID], 10.0, 185.0)  # the first buy's update arrives: 10 held
    fill(broker, second_buy, 10, 186.0, f"manual:{second}", "0000e0d5.000002b3.01.01")  # the second's lags

    # 10 held is the first buy's: the second's 10 stay reserved until a report holds 20.
    assert bridge.manual.unshown_fills({second_buy}, AAPL_ID, f"manual:{second}") == 10
    assert bridge.account_open_risk().total == Decimal("110.00")  # 10 x (185 - 180) + 10 x (186 - 180)
    broker.position(ACCOUNT, STOCK[AAPL_ID], 20.0, 185.5)
    assert bridge.manual.unshown_fills({second_buy}, AAPL_ID, f"manual:{second}") == 0


def test_fix2_low4_g06_a_market_buy_filled_above_the_ask_counts_at_its_fill_price(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, broker, clock, buy_id, parent_id, _ = manual_buy_world(tmp_path, monkeypatch, "/buy 10 AAPL stop 185")
    assert bridge.account_open_risk().total == Decimal("50.10")  # sized at the ask 190.01
    fill(broker, parent_id, 10, 191.0, f"manual:{buy_id}", "0000e0d5.000002c1.01.01")  # filled a dollar higher
    clock.advance(2)
    bridge.tick()
    assert manual_row(buy_id).status == "filled"
    assert bridge.account_open_risk().total == Decimal("60.00")  # 10 x (191 - 185) until the position shows it


def test_fix2_low4_g08_a_fill_ibkr_reports_by_status_before_its_execution_stays_reserved_and_unfitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, broker, clock, buy_id, parent_id, stop_id = manual_buy_world(tmp_path, monkeypatch,
                                                                         "/buy 10 AAPL stop 185")
    broker.orderStatus(parent_id, "Filled", 10, 0, 190.01)  # the status first; no execution, no position yet
    for _ in range(10):  # 20 s, past the stop fit's grace
        clock.advance(2)
        bridge.tick()

    assert manual_row(buy_id).status == "filled"
    assert broker.cancelled == [] and [order_id for order_id, _ in broker.placed] == [parent_id, stop_id]
    assert bridge.account_open_risk().total == Decimal("50.10")  # still reserved: 10 x (190.01 - 185)


# ---- v7c fix 3: review N18c (LOW-1, LOW-2) ------------------------------------------------------------------------


class OneLostAnswer(FakeBroker):
    """N18b/N18c probe P9: TWS drops the first reqAllOpenOrders (never answered) and answers every later one."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.all_calls = 0

    def reqAllOpenOrders(self) -> None:
        self.all_calls += 1
        if self.all_calls == 1:
            return
        super().reqAllOpenOrders()


def test_fix3_one_lost_answer_sends_nothing_more_pages_at_60s_and_never_holds_a_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18c probe P9: fix 2 sent a request every tick (two while unprotected), each waited its full timeout and was
    # answered, so the lost one never aged and the 60 s page never fired.
    ledger = manual_ledger(tmp_path, monkeypatch)
    broker = OneLostAnswer(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    broker.connect("127.0.0.1", 7497, CLIENT_ID)
    broker.reqAccountSummary(1, "All", "NetLiquidation")
    broker.reqPositions()  # 100 AAPL without a stop: unprotected, so the watch also re-reads before paging
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.sync_timeout = 0.2
    waits: list[float] = []
    real = bridge.open_orders_complete

    def complete(wanted: int, timeout: float) -> bool:
        waits.append(timeout)
        return real(wanted, timeout)

    bridge.open_orders_complete = complete
    capsys.readouterr()
    pages: list[str] = []
    for _ in range(41):  # 0..80 s
        bridge.tick()
        pages += [text for text in critical(events(capsys)) if "v7c_order_book_unavailable" in text]
        clock.advance(2)

    assert broker.all_calls == 1 and broker.open_orders_requested == 1  # nothing sent while the lost one is owed
    assert len(waits) == 1 and waits[0] == pytest.approx(0.2, abs=0.05)  # its own wait only: no tick waits after it
    assert pages == [
        "v7c_order_book_unavailable: TWS has not answered an open-orders request for 60s — the order book is"
        " unavailable: Telegram sells, /close and /closeall are refused, and stops placed in TWS no longer count"
        " for the day risk budget, so a position they protect blocks every entry; check TWS and restart the robot"]
    sell_id = queue_manual("/sell 10 AAPL", message_id=1, owner_shares=100, now=clock.value)
    bridge.tick()
    assert (manual_row(sell_id).status, manual_row(sell_id).reject_code) == ("rejected", "open_orders_unavailable")
    # v7c fix 4 (N18d-LOW-1): the owner's sell waits, in its own budget, for the owed answer before it refuses.
    assert broker.all_calls == 1 and len(waits) == 2 and broker.placed == []


def test_fix3_reconcile_never_waits_behind_an_owed_request(tmp_path: Path) -> None:
    broker = held_answers(positions={AAPL_ID: 100}, contracts=STOCK)
    bridge = make_bridge(tmp_path, broker)
    broker.hold = True
    assert bridge.refresh_order_book(timeout=0.01) is None  # request 1 is owed

    # Never at connect (the counters belong to the connection): the run exits 75 and the next one starts at 0.
    with pytest.raises(pb.ReconciliationError, match="still unanswered"):
        bridge.reconcile()
    assert broker.open_orders_requested == 1
    fresh = make_bridge(tmp_path, connected_broker(positions={AAPL_ID: 100}, contracts=STOCK))
    fresh.reconcile()  # a new connection's reconcile is never blocked
    assert (fresh.app.open_orders_requested, fresh.app.open_orders_answered) == (1, 1)


def test_fix3_low2_no_oversized_page_while_a_finished_buys_fills_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18c-LOW-2 (mutant J05): the settling arm of sizing_stops alone keeps this stop out of the oversize sum.
    ledger = manual_ledger(tmp_path, monkeypatch)
    buy_id = queue_manual("/buy 10 AAPL 185 stop 180", message_id=1, owner_shares=100)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    broker.position(ACCOUNT, STOCK[AAPL_ID], 100.0, 190.0)
    broker.book.append((7, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), tws_stop(7, 100, 181.0), "PreSubmitted",
                        100.0))  # the owner's own TWS stop on the 100 held
    broker.delay_echoes = True  # TWS reports the new orders late
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(parent_id, _), (stop_id, _)] = broker.placed
    broker.execDetails(-1, None, execution("0000e0d5.000003a1.01.01", parent_id, 4, 185.0, acctNumber=ACCOUNT,
                                           orderRef=f"manual:{buy_id}", side="BOT", time="20261013  10:00:30"))
    broker.orderStatus(parent_id, "Cancelled", 4, 6, 185.0)  # 4 bought, the rest cancelled; no position update yet
    clock.advance(2)
    bridge.tick()  # the buy is over: its 4 shares settle; its stop is not reported yet, so no fit is scheduled
    broker.delayed_echoes[1]()  # TWS now reports the attached stop (PreSubmitted, 10 sh)
    capsys.readouterr()
    for _ in range(3):
        clock.advance(2)
        bridge.tick()

    assert buy_id in bridge.manual.settling and stop_id not in bridge.manual.stop_fits  # only the settling arm
    assert bridge.account_open_risk().oversized == () and oversized_pages(events(capsys)) == []


# ---- v7c fix 4: review N18d (LOW-1..3) ------------------------------------------------------------------------------


def _held_buy_then_a_sale_reported_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, bought: int,
                                         ) -> tuple[Any, FakeBroker, Clock, int, int]:
    """N18d probe P20: 50 AAPL under the owner's TWS stop; a /buy 10 AAPL stop 185 buys ``bought`` (the rest
    cancelled) in the normal order; the owner cancels the TWS stop and sells all but 10 from Telegram (what
    the attached stop leaves free), and TWS reports that sale's position (10) before its execution."""
    sold = 40 + bought
    ledger = manual_ledger(tmp_path, monkeypatch)
    buy_id = queue_manual("/buy 10 AAPL stop 185", message_id=1, owner_shares=50)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 50}, contracts=STOCK)
    broker.own_in_all_open_orders = True
    broker.position(ACCOUNT, STOCK[AAPL_ID], 50.0, 190.0)
    broker.book.append((700, SimpleNamespace(conId=AAPL_ID, symbol="AAPL"), tws_stop(700, 50, 180.0), "PreSubmitted",
                        50.0))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(parent_id, _), (stop_id, _)] = broker.placed
    broker.execDetails(-1, None, execution("0000e0d5.000004a1.01.01", parent_id, bought, 190.01, acctNumber=ACCOUNT,
                                           orderRef=f"manual:{buy_id}", side="BOT", time="20261013  10:00:30"))
    broker.orderStatus(parent_id, "Filled" if bought == 10 else "Cancelled", bought, 10 - bought, 190.01)
    broker.position(ACCOUNT, STOCK[AAPL_ID], 50.0 + bought, 190.0)  # normal order: the buy is shown
    clock.advance(2)
    bridge.tick()
    broker.book.clear()  # the owner cancels his TWS stop on the old 50 and sells them from Telegram
    sell_id = queue_manual(f"/sell {sold} AAPL", message_id=2, owner_shares=50 + bought, now=clock.value)
    clock.advance(1)
    bridge.tick()
    [sell_order] = [order_id for order_id, item in broker.placed if item.action == "SELL" and item.orderType == "MKT"]
    broker.position(ACCOUNT, STOCK[AAPL_ID], 10.0, 190.0)  # reversed: the position first ...
    broker.execDetails(-1, STOCK[AAPL_ID], execution("0000e0d5.000004a2.01.01", sell_order, sold, 189.99,
                                                       acctNumber=ACCOUNT, orderRef=f"manual:{sell_id}", side="SLD",
                                                       time="20261013  10:00:40"))  # ... then the execution
    broker.orderStatus(sell_order, "Filled", sold, 0, 189.99)
    return bridge, broker, clock, parent_id, stop_id


def test_fix4_low3_a_sale_reported_before_its_execution_never_lets_the_fit_cancel_a_held_buys_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18d probe P20: fix 3 cancelled the stop of the 10 shares bought ("0 sh held") 15 s after the buy ended.
    bridge, broker, clock, parent_id, stop_id = _held_buy_then_a_sale_reported_first(tmp_path, monkeypatch, bought=10)
    capsys.readouterr()
    for _ in range(20):  # 40 s: no later position update
        clock.advance(2)
        bridge.tick()

    assert broker.cancelled == [] and [order_id for order_id, _ in broker.placed if order_id == stop_id] == [stop_id]
    lags = [text for text in critical(events(capsys)) if "its attached stop" in text]
    assert len(lags) == 1 and re.fullmatch(
        rf"AAPL: no position update \d+s after a sale of AAPL it does not show yet — its attached stop {stop_id} is"
        r" kept as it is until the position shows the shares; check TWS", lags[0])


def test_fix4_low3_the_fit_proceeds_once_the_sale_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, broker, clock, parent_id, stop_id = _held_buy_then_a_sale_reported_first(tmp_path, monkeypatch, bought=4)
    for _ in range(12):  # 24 s: past the fit's grace; the sale is not shown, so the stop stays at 10
        clock.advance(2)
        bridge.tick()
    assert broker.cancelled == [] and [order_id for order_id, _ in broker.placed if order_id == stop_id] == [stop_id]

    broker.position(ACCOUNT, STOCK[AAPL_ID], 10.0, 190.0)  # a later update shows the sale: 10 held, 4 of them bought
    for _ in range(2):
        clock.advance(2)
        bridge.tick()
    assert broker.cancelled == [] and [(order_id, sent.totalQuantity) for order_id, sent in broker.placed
                                       if order_id == stop_id] == [(stop_id, 10), (stop_id, 4)]  # fitted to 4


class SlowBook(FakeBroker):
    """N18d probe P21: TWS answers reqAllOpenOrders from its own thread after ``delay`` seconds."""

    delay = 0.3

    def reqAllOpenOrders(self) -> None:
        book = list(self.book)

        def answer() -> None:
            time.sleep(self.delay)
            for order_id, contract, item, status, _ in book:
                self.openOrder(order_id, contract, item, SimpleNamespace(status=status))
            self.openOrderEnd()

        threading.Thread(target=answer, daemon=True).start()


def test_fix4_low1_a_slow_tws_no_longer_refuses_an_owner_sell_while_the_watch_request_is_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # N18d probe P21: fix 3 refused this sell open_orders_unavailable at once.
    ledger = manual_ledger(tmp_path, monkeypatch)
    sell_id = queue_manual("/sell 50 AAPL", message_id=1, owner_shares=100)
    broker = SlowBook(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    broker.connect("127.0.0.1", 7497, CLIENT_ID)
    broker.reqAccountSummary(1, "All", "NetLiquidation")
    broker.reqPositions()
    _book_with_owner_stop(broker)
    broker.book = broker.book[1:]  # only the TSLA order: nothing reserves AAPL
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.sync_timeout = 3.0  # the desk's budget (10 s in production)
    assert bridge.refresh_order_book(timeout=0.1) is None  # the watch's request times out (3 s in production)
    bridge.manual.tick()  # the owner's sell is claimed while that answer is on its way

    [(_, sell)] = [(order_id, item) for order_id, item in broker.placed if item.action == "SELL"]
    assert (sell.orderType, sell.totalQuantity) == ("MKT", 50) and manual_row(sell_id).status == "sent"
    assert broker.open_orders_requested == broker.open_orders_answered == 2  # one outstanding at a time


def test_fix4_low1_after_the_owed_answer_the_desk_reads_its_own_book(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    sell_id = queue_manual("/sell 50 AAPL", message_id=1, owner_shares=100)
    broker = held_answers(bid=Decimal("189.99"), ask=Decimal("190.01"), positions={AAPL_ID: 100}, contracts=STOCK)
    _book_with_owner_stop(broker)
    owner_stop = broker.book.pop(0)  # the watch's request sees only the TSLA order
    broker.hold = True
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    assert bridge.refresh_order_book(timeout=0.01) is None  # request 1 is owed
    broker.book.insert(0, owner_stop)  # meanwhile the owner places his stop on all 100 AAPL in TWS

    def late_answer() -> None:  # TWS answers request 1 while the desk waits; later requests at once
        broker.deliver()
        broker.hold = False

    timer = threading.Timer(0.05, late_answer)
    timer.start()
    bridge.manual.tick()
    timer.join()

    # The desk's own request after the owed answer shows the new stop: the sell would leave 150 sh of sells.
    assert [item for _, item in broker.placed if item.action == "SELL"] == []
    assert (manual_row(sell_id).status, manual_row(sell_id).reject_code) == ("rejected", "insufficient_shares")
    assert broker.requests == 2


def test_fix4_low1_an_owed_refresh_never_returns_the_last_book(tmp_path: Path) -> None:
    # N18d mutant L02: no test had a fresh book followed by an owed request.
    broker = held_answers(positions={AAPL_ID: 100}, contracts=STOCK)
    _book_with_owner_stop(broker)
    bridge = make_bridge(tmp_path, broker)
    assert sorted(item.order_id for item in bridge.refresh_order_book(timeout=0.01) or ()) == [500, 501]
    broker.hold = True
    assert bridge.refresh_order_book(timeout=0.01) is None  # request 2 is owed
    assert bridge.refresh_order_book(timeout=0.01) is None  # still owed: None, never the last book
    assert bridge.refresh_order_book(timeout=0.01, wait_owed=True) is None  # the desk too, once its budget is out
    assert broker.requests == 2


def _two_buys_reported_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FakeBroker, Clock]:
    """N18d probe P22: two /buy 10 AAPL; A's position update (10) arrives before A's and B's executions."""
    ledger = manual_ledger(tmp_path, monkeypatch)
    a_id = queue_manual("/buy 10 AAPL 185 stop 180", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), contracts=STOCK)
    broker.own_in_all_open_orders = True
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    b_id = queue_manual("/buy 10 AAPL 185 stop 180", message_id=2, now=clock.value)
    clock.advance(1)
    bridge.tick()
    (a_parent, _), _, (b_parent, _), _ = broker.placed
    broker.position(ACCOUNT, STOCK[AAPL_ID], 10.0, 185.0)
    for exec_id, parent, ref in (("0000e0d5.000004b1.01.01", a_parent, a_id), ("0000e0d5.000004b2.01.01", b_parent,
                                                                                 b_id)):
        broker.execDetails(-1, None, execution(exec_id, parent, 10, 185.0, acctNumber=ACCOUNT, orderRef=f"manual:{ref}",
                                               side="BOT", time="20261013  10:00:50"))
        broker.orderStatus(parent, "Filled", 10, 0, 185.0)
    return bridge, broker, clock


def test_fix4_low2_two_buys_raise_no_oversized_page_while_the_report_catches_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # N18d probe P22: fix 3 paged "short 10 sh — resize or cancel the extra stop" on two stops covering 20 real shares.
    bridge, broker, clock = _two_buys_reported_early(tmp_path, monkeypatch)
    capsys.readouterr()
    for _ in range(11):  # 22 s: the fits end (both stops fit their buys), the report still says 10
        clock.advance(2)
        bridge.tick()
    assert bridge.account_open_risk().oversized  # the condition is there, the page waits for the grace
    broker.position(ACCOUNT, STOCK[AAPL_ID], 20.0, 185.0)  # B's own update arrives: 20 held
    for _ in range(10):
        clock.advance(2)
        bridge.tick()

    assert oversized_pages(events(capsys)) == [] and bridge.account_open_risk().oversized == ()
    assert broker.cancelled == [] and all(item.totalQuantity == 10 for _, item in broker.placed
                                          if item.orderType == "STP")


def test_fix4_low2_a_persistent_oversell_pages_after_the_grace_and_again_in_a_later_episode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge, broker, clock = _two_buys_reported_early(tmp_path, monkeypatch)
    capsys.readouterr()
    pages: list[str] = []
    for _ in range(20):  # 40 s and the report never catches up
        clock.advance(2)
        bridge.tick()
        pages += oversized_pages(events(capsys))
    page = ("v7c_stop_oversized AAPL: working stops sell 20 sh but cover 10 held: if they all fire the account is short"
            " 10 sh — resize or cancel the extra stop in TWS")
    assert pages == [page]  # once, after the grace

    broker.position(ACCOUNT, STOCK[AAPL_ID], 20.0, 185.0)  # the episode ends ...
    clock.advance(2)
    bridge.tick()
    broker.position(ACCOUNT, STOCK[AAPL_ID], 10.0, 185.0)  # ... and the owner sells 10 in TWS: a new one
    for _ in range(9):
        clock.advance(2)
        bridge.tick()
    assert oversized_pages(events(capsys)) == [page]  # pages again, after its own grace
