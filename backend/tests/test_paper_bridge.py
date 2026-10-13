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
        status = self.order_status.get(order_id, "PreSubmitted" if order.orderType == "STP" else "Submitted")
        if self.echo_orders:
            echo = copy.copy(order)
            echo.clientId = CLIENT_ID
            self.openOrder(order_id, SimpleNamespace(symbol="", conId=contract.conId), echo,
                           SimpleNamespace(status=status))
        self.orderStatus(order_id, status, 0, order.totalQuantity, 0)

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
        strategy="momo", strategy_name="MOMO_HOD_V1", qty=qty, entry="50.00", stop="49.50",
        target="51.00", parent=parent, target_id=parent + 1, stop_id=parent + 2,
        sent_at=DECIDED.astimezone(timezone.utc).isoformat(), status=status, filled=filled,
        avg_entry="50.0000" if filled else "0", exit_filled=exit_filled, **fields,
    )


def decision_row(decision_id: str, symbol: str, *, strategy: str = "MOMO_HOD_V1",
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
    from trading.contracts import FixedUniverse, RuleCondition, StrategySpec, StrategyState
    from trading.storage.repository import create_strategy, transition_strategy

    spec = StrategySpec(
        name=name,
        universe=FixedUniverse(symbols=("AAPL",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale=f"{name} bridge fixture.",
    )
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
    momo = await _shadow_strategy("MOMO_HOD_V1")
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
    assert [(row.symbol, row.strategy_name) for row in rows] == [("AAPL", "MOMO_HOD_V1"), ("MMED", "ORB_V1")]

    broker = connected_broker(bid=Decimal("50.10"))
    bridge = make_bridge(tmp_path, broker, db=db_path)
    capsys.readouterr()
    bridge.tick()
    bridge.tick()

    out = events(capsys)
    assert [item for item in out if item[0] == "SKIP"] == [("SKIP", "MMED (ORB_V1): shadow-only strategy")]
    assert [item for item in out if item[0] == "SENT"] == [
        ("SENT", "AAPL (MOMO_HOD_V1): BUY 100 @ 50.00 | stop 49.50 | target 51.00 (orders 100-102)")
    ]
    assert [order_id for order_id, _ in broker.placed] == [100, 101, 102]
    momo, orb = bridge.brackets[ids["momo"]], bridge.brackets[ids["orb"]]
    assert (momo.status, momo.strategy, momo.strategy_name) == ("sent", "momo", "MOMO_HOD_V1")
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
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert any(kind == "INFO" and text.startswith("PAPER bridge v5 connected") for kind, text in out)
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
    pb.log("FILL", "AAPL: 100/100 sh @ 50.0000 (MOMO_HOD_V1)")
    pb.log("BOGUS", "multi\nline  message")
    assert events(capsys) == [
        ("FILL", "AAPL: 100/100 sh @ 50.0000 (MOMO_HOD_V1)"),
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
    assert ("SKIP", f"BIG (MOMO_HOD_V1): {reason}") in events(capsys)


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
        assert bridge.brackets["n"].skip_reason == "max 3 positions/entries already active"


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
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert ("SKIP", "OLDX (MOMO_HOD_V1): decision expired (20s old)") in out
    assert ("SKIP", "PLNX (MOMO_HOD_V1): decision expired (20s old)") in out


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
    assert ("SKIP", "AAPL (MOMO_HOD_V1): decision expired (16s old)") in events(capsys)


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
        "1528474519", 3, 30, "autotrader:held", "GTC", ACCOUNT
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
    assert ("SKIP", f"AAPL (MOMO_HOD_V1): {reason}") in events(capsys)


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
    placement = pb.order("SELL", kind, 100, ACCOUNT, "autotrader:held", tif="GTC", parentId=30,
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
    row_id = queue_manual("/buy 10 AAPL", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    capsys.readouterr()
    bridge.tick()

    [(order_id, sent)] = broker.placed
    assert (sent.action, sent.orderType, sent.totalQuantity, sent.tif, sent.transmit, sent.account) == (
        "BUY", "MKT", 10, "DAY", True, ACCOUNT
    )
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
    assert manual_lines(out) == [
        f"SENT BUY 10 AAPL MKT #{row_id[:6]} (order {order_id})",
        f"FILLED BUY 10 AAPL MKT @ 190.0100 #{row_id[:6]}",
    ]
    assert not [kind for kind, _ in out if kind in {"SENT", "FILL", "EXIT", "CRITICAL"}]  # the notifier skips MANUAL


@pytest.mark.parametrize(("text", "expected"), [
    ("/buy 10 AAPL 185", ("BUY", "LMT", 10, "lmtPrice", 185.0)),
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
    lost = queue_manual("/buy 10 AAPL", message_id=1)
    known = queue_manual("/buy 5 TSLA", message_id=2)
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
    row_id = queue_manual("/buy 10 AAPL 185", message_id=1)
    other = queue_manual("/buy 3 TSLA 185", message_id=2)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    bridge.tick()
    order_id, other_id = (order_id for order_id, _ in broker.placed)

    queue_manual(f"/cancel {row_id[:6]}", message_id=3, now=clock.value)
    capsys.readouterr()
    bridge.tick()
    bridge.tick()
    assert broker.cancelled == [order_id]  # once, and only that row's own order
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
    row_id = queue_manual("/buy 10 AAPL", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(order_id, _)] = broker.placed
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
    row_id = queue_manual("/buy 10 AAPL", message_id=1, now=ny(15, 44, 50))
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
    queue_manual("/buy 10 AAPL 185", message_id=1)
    queue_manual("/buy 5 TSLA 185", message_id=2)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    bridge.tick()
    assert len(broker.placed) == 2
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
    first = queue_manual("/buy 10 AAPL 185", message_id=1)
    second = queue_manual("/buy 3 TSLA 185", message_id=2)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    bridge.tick()
    first_id, second_id = (order_id for order_id, _ in broker.placed)
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
    row_id = queue_manual("/buy 10 AAPL", message_id=1)
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


@pytest.mark.parametrize("text", ["/close AAPL", "/sell 100 AAPL", "/sell all AAPL", "/buy 10 AAPL"])
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
    row_id = queue_manual("/buy 10 AAPL", message_id=1)  # queued while the fence was off
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
    row_id = queue_manual("/buy 10 AAPL", message_id=1)
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
    row_id = queue_manual("/buy 10 AAPL", message_id=1)
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
    row_id = queue_manual("/buy 10 AAPL", message_id=1)
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
    assert bridge.brackets["d1"].skip_reason == "daily loss limit reached"  # 2% of 665,000


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
    row_id = queue_manual("/buy 10 AAPL", message_id=1)
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
    for message_id, text in ((1, "/buy 800 AAPL"), (2, "/buy 800 TSLA")):  # ~152,000 each
        queue_manual(text, message_id=message_id)
        assert asyncio.run(mo.claim_next_order(MANUAL_T0 + timedelta(seconds=1))) is not None
    asyncio.run(mo.mark_unknown_sending_rows(set(), now=MANUAL_T0 + timedelta(seconds=1)))  # both unknown
    row_id = queue_manual("/buy 200 NVDA", message_id=3)  # +38,000: over 50% of 665,000 with the two above
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
    lost = queue_manual("/buy 10 TSLA", message_id=2, now=yesterday)
    today = queue_manual("/buy 5 NVDA 185", message_id=3)
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
    old = queue_manual("/buy 1 TSLA", message_id=1)
    assert asyncio.run(mo.claim_next_order(MANUAL_T0 + timedelta(seconds=1))) is not None
    asyncio.run(mo.update_order_status(old, "sent", now=MANUAL_T0, ibkr_order_id=150))
    done = bracket("done", "DONE", status="closed", parent=120, filled=100, exit_filled=100)  # legs 120-122
    broker = connected_broker(bid=Decimal("50.10"))  # TWS's sequence restarted at 100
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"done": done})
    bridge.decisions = lambda: [decision_row("d1", "AAPL")]
    bridge.startup()
    bridge.tick()

    assert [order_id for order_id, _ in broker.placed] == [151, 152, 153]  # never 100-102 again


def test_l3_a_stored_order_id_ibkr_reports_as_another_order_is_never_acted_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trading.execution import manual_orders as mo

    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL 185", message_id=1)
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
    row_id = queue_manual("/buy 10 AAPL 185", message_id=1)
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"))
    clock = Clock(MANUAL_T0 + timedelta(seconds=2))
    bridge = desk_bridge(tmp_path, broker, ledger, clock=clock)
    bridge.tick()
    [(order_id, _)] = broker.placed
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
    row_id = queue_manual("/buy 10 AAPL", message_id=1, now=ny(15, 44, 40))
    broker = connected_broker(bid=Decimal("189.99"), ask=Decimal("190.01"), live_orders={31: "Submitted", 32: "PreSubmitted"},
                              positions={300: 100})
    broker.reqOpenOrders()
    clock = Clock(ny(15, 44, 45))
    bridge = desk_bridge(tmp_path, broker, ledger, brackets={"held": held}, clock=clock)
    bridge.tick()
    assert broker.placed == [] and manual_row(row_id).status == "queued"  # waits for the flatten

    clock.value = ny(15, 45, 0)
    bridge.tick()
    assert [sent.orderRef for _, sent in broker.placed] == ["autotrader:held", f"manual:{row_id}"]  # flatten first


def test_f1_live_bot_legs_block_owner_orders_by_symbol_even_without_a_contract_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = manual_ledger(tmp_path, monkeypatch)
    row_id = queue_manual("/buy 10 AAPL", message_id=1)
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
    queue_manual("/buy 5 TSLA", message_id=1)
    bridge = desk_bridge(tmp_path, broker, ledger, clock=Clock(MANUAL_T0 + timedelta(seconds=2)))
    bridge.startup()
    bridge.tick()

    [(order_id, sent)] = broker.placed
    assert order_id == 102 and sent.action == "BUY"  # v6 reused 100, the id of a live GTC leg


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
