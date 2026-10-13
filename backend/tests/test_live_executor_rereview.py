"""Regression tests for the execution engine's re-review at 3579e92 (READY-FOR-DRILL, R-1 to R-6).

Each test is a reviewer probe with the fixed expectation, named after its
finding; each fails on the behaviour the finding described.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import time
from typing import Any

import pytest

from tests.fakes.engine_harness import CLIENT_ID, Clock, Harness, build, ny, stepping
from tests.test_live_executor_review import HELD, open_bracket, owner_sells, sells
from trading.execution.live_executor import (
    BRIDGE_PROCESS_PATTERN,
    ExecutionRefused,
    bridge_database,
    latest_bridge_heartbeat,
    running_bridge_processes,
)


def without_baseline(h: Harness, bracket: Any) -> None:
    """A bracket staged before entry baselines existed: the completion time alone decides."""
    h.engine.entry_baselines.pop(bracket.id)


# ---- R-2: a completed sell's filled quantity of 0 is unknown, never "nothing sold" ---------------------


@pytest.mark.parametrize(("status", "filled", "engine_sells"), [
    ("Filled", 0, []),  # the probe: filledQuantity 0.0 on a Filled order made the engine sell 100 again
    ("Cancelled", 0, []),  # 0 may be an empty field: the whole order may have sold
    ("Cancelled", 40, [(103, "SELL", "MKT", 60)]),  # a reported partial fill counts as reported
])
async def test_r2_a_completed_sell_without_a_fill_quantity_counts_its_whole_size(
    tmp_path, monkeypatch, status: str, filled: int, engine_sells: list[tuple[int, str, str, int]]
) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    sale = owner_sells(h, 100, status=status)
    sale.filled = filled  # what TWS reports as filledQuantity; the position does not show any of it yet
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert sells(h) == engine_sells


def test_r2_completed_sell_shares() -> None:
    from decimal import Decimal

    from trading.execution.live_executor import completed_sell_shares

    hundred = Decimal("100")
    assert completed_sell_shares("Filled", hundred, Decimal("0")) == hundred
    assert completed_sell_shares("Filled", hundred, None) == hundred
    assert completed_sell_shares("Filled", hundred, Decimal("40")) == hundred
    assert completed_sell_shares("Cancelled", hundred, Decimal("0")) == hundred
    assert completed_sell_shares("Cancelled", hundred, None) == hundred
    assert completed_sell_shares("Cancelled", hundred, Decimal("40")) == Decimal("40")


# ---- R-3: only other orders since the entry are reserved ---------------------------------------------------


@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "completion-time"])
async def test_r3_the_engines_own_earlier_bracket_is_never_another_orders_sale(
    tmp_path, monkeypatch, baseline: bool
) -> None:
    h = await build(tmp_path, monkeypatch)
    first = await open_bracket(h)
    h.tws.fill(first.order_id("T"), 100, "51.00")  # bracket A exits at its target
    await h.engine.tick()
    assert first.state == "closed"
    h.clock.advance(30)  # a new HELD setup 30 s later: re-entry in the same symbol
    second = await open_bracket(h)
    if not baseline:
        without_baseline(h, second)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    # The probe counted A's target sell as another order's unshown sale: no flatten, held overnight.
    assert [(oid, kind, qty) for oid, _, kind, qty in sells(h, after=6)] == [(second.order_id("T"), "LMT", 100)]
    assert second.flatten_state == "repricing"


@pytest.mark.parametrize("restart", [False, True], ids=["running", "after-a-restart"])
async def test_r3_an_owner_sale_before_the_entry_with_a_zone_less_time_reserves_nothing(
    tmp_path, monkeypatch, restart: bool
) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    h.clock.value = ny(9, 41)
    owner_sells(h, 100, status="Filled").completed_at = "20261013 09:41:00"  # no zone: unreadable
    h.clock.value = ny(10, 0, 5)
    await h.engine.start()
    await open_bracket(h)
    if restart:  # the baseline is kept with the bracket in the ledger
        h = await h.restart()
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert sells(h) == [(101, "SELL", "LMT", 100)]  # the entry baseline saw it completed before the entry


async def test_r3_without_a_baseline_a_sale_timed_before_the_entry_reserves_nothing(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    h.clock.value = ny(9, 40)
    owner_sells(h, 100, status="Filled")  # completedTime 20261013 09:40:00 America/New_York
    h.clock.value = ny(10, 0, 5)
    await h.engine.start()
    bracket = await open_bracket(h)
    without_baseline(h, bracket)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert sells(h) == [(101, "SELL", "LMT", 100)]  # the completion time places it 20 minutes before the entry


async def test_r3_without_a_baseline_an_unreadable_time_still_counts_as_after_the_entry(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    h.clock.value = ny(9, 41)
    owner_sells(h, 100, status="Filled").completed_at = "20261013 09:41:00"
    h.clock.value = ny(10, 0, 5)
    await h.engine.start()
    bracket = await open_bracket(h)
    without_baseline(h, bracket)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(15, 45, 1)
    h.events()
    await h.engine.tick()

    # In doubt the shares stay reserved: no sell, the stop stays live, and the owner is told.
    assert sells(h) == [] and bracket.flatten_state == "skipped"
    assert any("sold by other orders that the position does not show yet" in text for text in h.critical())


# ---- R-1: a missing snapshot never ends a flatten for good -------------------------------------------------


async def test_r1_a_flatten_without_a_fresh_snapshot_waits_and_sells_once_tws_answers(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.answer_snapshots = False
    h.clock.value = ny(15, 45, 1)
    h.events()
    for _ in range(3):
        await h.engine.tick()
        h.clock.advance(20)
    assert sells(h) == [] and bracket.flatten_state == "none"  # nothing sent, nothing given up
    assert h.critical().count("HELD: flatten waits for a fresh TWS snapshot — nothing sent; tried again every"
                              " tick until 15:50 ET") == 1

    h.tws.answer_snapshots = True  # TWS answers again at 15:46: the same attempt goes on, once
    await h.engine.tick()
    await h.engine.tick()
    assert sells(h) == [(101, "SELL", "LMT", 100)] and bracket.flatten_state == "repriced"


async def test_r1_a_flatten_start_that_never_gets_a_snapshot_fails_at_close_minus_10_minutes(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.answer_snapshots = False
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    h.clock.value = ny(15, 50)
    h.tws.answer_snapshots = True  # too late: no try after the deadline
    h.events()
    await h.engine.tick()

    assert sells(h) == [] and bracket.flatten_state == "failed"
    assert "HELD: flatten not sent — no fresh TWS snapshot by 15:50 ET; act in TWS" in h.critical()
    for _ in range(3):  # a recorded attempt is never started again [N5]
        h.clock.advance(30)
        await h.engine.tick()
    assert sells(h) == []


async def cancels_confirmed_without_a_snapshot(h: Harness) -> Any:
    """The fallback's cancels are confirmed while TWS stops answering snapshots."""
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])  # the target is gone: the flatten needs the fallback
    h.tws.confirm_cancels = False
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert bracket.flatten_state == "cancelling" and h.tws.cancelled == [102]
    h.tws.confirm_pending_cancels()
    h.tws.answer_snapshots = False
    h.events()
    return bracket


async def test_r1_the_fallback_sell_waits_for_a_fresh_snapshot_and_its_shares_are_flagged_at_once(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await cancels_confirmed_without_a_snapshot(h)
    h.clock.advance(2)
    await h.engine.tick()

    # The probe: 'failed', stop and target cancelled, no sell even after TWS answered again.
    assert sells(h) == [] and bracket.flatten_state == "cancelling"
    assert h.critical() == [
        "HELD: fallback market sell not sent yet (TWS did not report a fresh snapshot) — target and stop are"
        " cancelled; tried again every tick until 16:00 ET; act in TWS if it persists",
        "HELD: 100 sh UNPROTECTED — the flatten fallback cancelled target and stop and no market sell is working;"
        " act in TWS",
    ]
    h.tws.answer_snapshots = True
    h.clock.advance(2)
    await h.engine.tick()
    assert sells(h) == [(103, "SELL", "MKT", 100)]
    await h.engine.tick()
    assert len(sells(h)) == 1


async def test_r1_a_fallback_sell_that_never_gets_a_snapshot_fails_at_the_close(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await cancels_confirmed_without_a_snapshot(h)
    h.clock.value = ny(15, 55)
    h.engine.pause = stepping(h.clock, 30)

    assert await h.engine.finish() == 0

    assert sells(h) == [] and bracket.flatten_state == "failed"
    assert h.clock.value >= ny(16, 0)  # tried until the close, past the 15:58 final alert
    assert ("HELD: fallback market sell not sent by the 16:00 ET close (TWS did not report a fresh snapshot) —"
            " 100 sh without target or stop; act in TWS") in h.critical()


async def test_r1_a_rejected_fallback_sell_leaves_the_shares_flagged_unprotected(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert sells(h) == [(103, "SELL", "MKT", 100)]
    sell = h.tws.orders[(CLIENT_ID, 103)]
    sell.status = "Cancelled"  # TWS rejected the market sell
    h.tws.status_only(sell)
    h.events()
    await h.engine.tick()

    assert ("HELD: 100 sh UNPROTECTED — the flatten fallback cancelled target and stop and no market sell is"
            " working; act in TWS") in h.critical()


# ---- R-4: a restart after the stop time finishes or flags a fallback in flight ---------------------------------


async def fallback_cut_off_before_its_sell(h: Harness) -> Any:
    """Target and stop cancelled by the fallback; the engine stops before its market sell."""
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])

    async def locked(client_id: int) -> int:
        raise sqlite3.OperationalError("database is locked")

    h.engine.ledger.max_order_id = locked  # type: ignore[method-assign]
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert bracket.flatten_state == "cancelling" and h.tws.cancelled == [102] and sells(h) == []
    return bracket


async def test_r4_a_restart_after_the_stop_time_finishes_the_fallback_sell_before_the_close(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    await fallback_cut_off_before_its_sell(h)
    clock = Clock(ny(15, 56))
    restarted = await h.restart(start=False, clock=clock)
    restarted.engine.pause = stepping(clock, 2)
    restarted.events()

    assert await restarted.engine.run() == 0

    assert sells(h) == [(103, "SELL", "MKT", 100)]  # the probe sent nothing and only said "still open"
    assert ("CRITICAL", "HELD: 100 sh UNPROTECTED — the flatten fallback was cancelling target and stop when the"
            " engine stopped and its market sell was never sent; act in TWS") in restarted.events()
    assert clock.value < ny(16, 0)


async def test_r4_after_the_close_a_fallback_in_flight_is_only_flagged(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await fallback_cut_off_before_its_sell(h)
    restarted = await h.restart(start=False, clock=Clock(ny(16, 5)))
    restarted.events()
    requests = len(h.tws.requests)

    assert await restarted.engine.run() == 0

    assert sells(h) == [] and len(h.tws.requests) == requests  # nothing is sent after the close
    assert restarted.critical() == [
        "HELD: 100 sh UNPROTECTED — the flatten fallback was cancelling target and stop when the engine stopped"
        " and its market sell was never sent; act in TWS"
    ]


# ---- R-5: the bridge interlock ----------------------------------------------------------------------------

BRIDGE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts_local" / "paper_bridge.py"


def test_r5_the_interlock_reads_the_database_the_bridge_writes_its_heartbeat_into() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("paper_bridge_for_the_interlock", BRIDGE_SCRIPT)
    assert spec is not None and spec.loader is not None
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge  # dataclasses resolve annotations through sys.modules
    try:
        spec.loader.exec_module(bridge)
    finally:
        sys.modules.pop(spec.name, None)
    assert bridge_database() == bridge.DB  # whatever database the engine itself is given


async def test_r5_an_engine_on_another_database_still_sees_the_bridges_heartbeat(tmp_path, monkeypatch) -> None:
    from config import cfg
    from db.core import init_db
    from trading.execution.manual_orders import write_bridge_heartbeat

    bridge_db = tmp_path / "bridge" / "autotrader.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(bridge_db))
    await init_db()
    await write_bridge_heartbeat("running", now=ny(10, 0), pid=4242)  # the bridge's ledger
    h = await build(tmp_path / "engine", monkeypatch, start=False)  # the engine runs on another database
    h.engine.bridge_database = bridge_db
    h.engine.bridge_heartbeat = h.engine.read_bridge_heartbeat  # the engine's own reader (the harness stubs it)

    with pytest.raises(ExecutionRefused) as refused:
        await h.engine.start()
    assert refused.value.code == "bridge_running" and "heartbeat 5s ago" in str(refused.value)


async def test_r5_the_heartbeat_is_read_only_and_anything_unreadable_counts_as_a_running_bridge(
    tmp_path, monkeypatch
) -> None:
    absent = tmp_path / "absent.db"
    assert await latest_bridge_heartbeat(absent) is None and not absent.exists()  # never creates a database
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert await latest_bridge_heartbeat(empty) is None  # no bridge ever wrote a heartbeat there
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a database " * 100)
    with pytest.raises(sqlite3.DatabaseError):
        await latest_bridge_heartbeat(corrupt)

    h = await build(tmp_path / "engine", monkeypatch, start=False)
    h.engine.bridge_database = corrupt
    h.engine.bridge_heartbeat = h.engine.read_bridge_heartbeat
    with pytest.raises(ExecutionRefused) as refused:
        await h.engine.start()
    assert "the bridge heartbeat is unreadable" in str(refused.value)


def test_r5_the_process_pattern_matches_the_supervisors_launch_line() -> None:
    from datetime import date

    from trading.operations.supervisor import BRIDGE_CHILD, plan_session
    from trading.scheduler.sessions import market_session

    session = market_session(date(2026, 10, 13))
    assert session is not None
    [bridge] = [plan for plan in plan_session(session) if plan.name == BRIDGE_CHILD]
    assert re.search(BRIDGE_PROCESS_PATTERN, " ".join(bridge.argv))


@pytest.mark.skipif(sys.platform != "darwin" or not Path("/usr/bin/pgrep").is_file(), reason="macOS pgrep only")
def test_r5_real_pgrep_finds_every_process_carrying_the_bridge_in_its_arguments(tmp_path) -> None:
    def dummy(*arguments: str) -> subprocess.Popen[bytes]:
        # /bin/sh, never python: the supervisor's own scan cannot take it for an unsupervised bridge.
        return subprocess.Popen(["/bin/sh", "-c", "sleep 30; true", "dummy", *arguments], start_new_session=True)

    script = dummy(str(tmp_path / "scripts_local" / "paper_bridge.py"), "--date", "2026-10-13")
    module = dummy("-m", "scripts_local.paper_bridge")
    other = dummy(str(tmp_path / "tests" / "test_paper_bridge.py"))
    try:
        deadline = time.monotonic() + 5
        found: set[int] = set()
        while time.monotonic() < deadline and not {script.pid, module.pid} <= found:
            found = {pid for pid, _ in running_bridge_processes()}  # the real /usr/bin/pgrep
            time.sleep(0.05)
        assert {script.pid, module.pid} <= found
        assert other.pid not in found
    finally:
        for process in (script, module, other):
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)


# ---- R-6: the digest binds the whole import graph ------------------------------------------------------------

BACKEND = Path(__file__).resolve().parents[1]


def _module_file(module: str) -> Path | None:
    path = BACKEND.joinpath(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _imported_modules(path: Path, *, top_level_only: bool = False) -> set[str]:
    """Every module an ``import`` statement in the file may load (relative ones resolved)."""
    package = path.relative_to(BACKEND).parent.parts
    found: set[str] = set()
    tree = ast.parse(path.read_text())
    for node in (tree.body if top_level_only else ast.walk(tree)):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            parts = [*package[: len(package) - node.level + 1]] if node.level else []
            base = ".".join([*parts, *(node.module.split(".") if node.module else [])])
            found |= {base, *(f"{base}.{alias.name}" for alias in node.names)}
    return found


def engine_import_graph(entry_points: tuple[str, ...], process_files: tuple[str, ...] = ()) -> set[str]:
    """Backend files a running engine may execute: the entry points, everything they import, and every
    package ``__init__`` on the way (importing a.b.c runs a/__init__ and a/b/__init__ first). Process
    files count with what they import when the process starts (their other commands' lazy imports never
    run in the engine's process)."""
    seen: set[Path] = set()
    todo = [BACKEND / relative for relative in (*entry_points, *process_files)]
    top_level = {BACKEND / relative for relative in process_files}
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        for module in _imported_modules(path, top_level_only=path in top_level):
            parts = module.split(".")
            packages = [_module_file(".".join(parts[:end])) for end in range(1, len(parts))]
            todo += [item for item in [*packages, _module_file(module)] if item is not None]
    return {path.relative_to(BACKEND).as_posix() for path in seen}


def test_r6_the_code_digest_binds_the_engines_whole_import_graph() -> None:
    from trading.execution.promotion import ENGINE_ENTRY_POINTS, ENGINE_PROCESS_FILES, ENGINE_SOURCE_FILES

    graph = engine_import_graph(ENGINE_ENTRY_POINTS, ENGINE_PROCESS_FILES)
    # The review's examples: package __init__ modules imported for their side effects, and db/core.py;
    # the second review's: the process entry files.
    assert {"trading/__init__.py", "trading/risk/__init__.py", "trading/scheduler/__init__.py",
            "db/core.py", "trading/cli.py", "autotraderctl.py"} <= graph
    missing = graph - set(ENGINE_SOURCE_FILES)
    assert not missing, f"the engine runs {sorted(missing)}, which the promotion does not bind"
    assert all((BACKEND / relative).is_file() for relative in ENGINE_SOURCE_FILES)


def test_r6_the_import_graph_follows_package_inits_and_lazy_imports() -> None:
    graph = engine_import_graph(("trading/execution/live_executor.py",))
    assert "trading/execution/manual_orders.py" in graph  # imported inside a function
    assert "trading/scheduler/decision_keys.py" in graph  # only through trading/scheduler/__init__.py
    assert "config.py" in graph  # through db/core.py


def test_r6_the_digest_binds_the_installed_ibapi_and_the_engine_refuses_another(monkeypatch) -> None:
    import trading.execution.promotion as promotion
    from trading.execution.promotion import ENGINE_IBAPI_VERSION, engine_code_digest, installed_ibapi_version

    requirements = (BACKEND / "requirements.txt").read_text().splitlines()
    assert f"ibapi=={ENGINE_IBAPI_VERSION}" in requirements  # the reviewed release is the pinned one
    assert installed_ibapi_version() == ENGINE_IBAPI_VERSION
    reviewed = engine_code_digest()
    monkeypatch.setattr(promotion, "installed_ibapi_version", lambda: "10.19.4")
    assert engine_code_digest() != reviewed  # a grant never carries over to another ibapi


async def test_r6_the_engine_refuses_to_start_on_another_ibapi(tmp_path, monkeypatch) -> None:
    from trading.execution import live_executor
    from trading.execution.promotion import ExecutionAuthorityError

    h = await build(tmp_path, monkeypatch, start=False)
    monkeypatch.setattr(live_executor, "installed_ibapi_version", lambda: "10.19.4")
    with pytest.raises(ExecutionAuthorityError) as refused:
        await h.engine.start()
    assert refused.value.code == "code_mismatch" and "ibapi 10.19.4 is installed" in str(refused.value)
    assert h.tws.placed == []
