"""The dashboard's read-only view of the AI day manager: ``/api/live/manager/*``.

Five GET routes, no POST, no autopilot route: the switch, the plan in force,
the plan history, the managed reviewer's verdicts and the plan spend, read
from a database the code migrated itself (v16) and reported unavailable on
one it has not (v13, v15). Nothing here writes through the routes.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import aiosqlite
import pytest

from tests.schema_support import apply_up_to
from tests.test_live_routes import _bearer, _client, _run, _shadow_strategy
from trading.contracts import Candidate
from trading.data import CandidateRepository

NOW = datetime(2026, 10, 13, 14, 5, tzinfo=timezone.utc)  # Tuesday 10:05 New York
SESSION = date(2026, 10, 13)
TODAY = SESSION.isoformat()
MANAGER_PATHS = (
    "/api/live/manager/status",
    "/api/live/manager/plan",
    "/api/live/manager/plans",
    "/api/live/manager/verdicts",
    "/api/live/manager/spend",
)
FP_A, FP_B, FP_C, FP_D = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
PLAN_0920 = "20261013-0920-1"
PLAN_0946_FAILED = "20261013-0946-1"
PLAN_0946 = "20261013-0946-2"


def _utc(hour: int, minute: int, second: int = 0, *, day: date = SESSION) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def manager_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[object, Path, _Clock, Path]]:
    from config import cfg
    from main import app
    from routers.live_manager_routes import notifier_state_directory
    from routers.live_routes import live_clock

    database = tmp_path / "manager_routes.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(database))
    clock = _Clock(NOW)
    notify_dir = tmp_path / "notify"
    app.dependency_overrides[live_clock] = lambda: clock
    # Never the owner's real notifier state.
    app.dependency_overrides[notifier_state_directory] = lambda: notify_dir
    try:
        yield app, database, clock, notify_dir
    finally:
        app.dependency_overrides.pop(live_clock, None)
        app.dependency_overrides.pop(notifier_state_directory, None)


async def _users_table(path: Path) -> None:
    """A database the auth layer accepts a demo token for, whatever its schema version."""
    from auth import seed_demo_user

    async with aiosqlite.connect(str(path)) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE, "
            "password_hash TEXT, created_at TEXT, settings TEXT NOT NULL DEFAULT '{}')"
        )
        await db.commit()
        await seed_demo_user(db)


async def _get(app: object, path: str) -> tuple[int, dict]:
    async with _client(app) as client:
        response = await client.get(path, headers=_bearer())
    return response.status_code, response.json()


def _gates(**overrides: bool) -> dict[str, object]:
    from trading.manager.plan import GateStates

    return GateStates(**overrides).payload()


async def _seed_plans() -> None:
    from trading.manager.plan import DayPlan, FocusEntry, StrategyStep
    from trading.manager.store import PlanRecord, insert_plan

    first = DayPlan(
        regime="trend_up",
        strategies={
            "momo": StrategyStep(step=Decimal("1"), reason="trend day"),
            "orb": StrategyStep(step=Decimal("0"), reason="range-bound open"),
        },
        focus=(FocusEntry(con_id=4931, strategies=("momo",), note="gap and go"),),
    )
    await insert_plan(
        PlanRecord(
            plan_id=PLAN_0920, session_date=SESSION, slot="09:20", created_at=_utc(13, 20, 40),
            manager_version="manager-v1", prompt_id="day-plan-v1", model="claude-opus-5-5",
            outcome="valid", candidates=(4931, 265598, 777), gates=_gates(risk_off=True),
            dropped=({"kind": "focus", "index": 1, "token": "ai_invalid_entry",
                      "detail": "con_id_not_offered", "widening": True},),
            plan=first, valid_until=_utc(14, 35, 40), context_digest="e" * 64,
            input_tokens=6500, output_tokens=4200, cost_usd=Decimal("0.4123"), latency_ms=8400,
        )
    )
    await insert_plan(
        PlanRecord(
            plan_id=PLAN_0946_FAILED, session_date=SESSION, slot="09:46",
            created_at=_utc(13, 47, 30), manager_version="manager-v1", prompt_id="day-plan-v1",
            model="claude-opus-5-5", outcome="ai_timeout", candidates=(4931, 265598, 777),
            gates=_gates(), latency_ms=90000,
        )
    )
    second = DayPlan(
        regime="range",
        strategies={
            "momo": StrategyStep(step=Decimal("0.5"), reason="choppy tape"),
            "orb": StrategyStep(step=Decimal("1"), reason="clean ranges"),
        },
        focus=(
            FocusEntry(con_id=4931, strategies=("momo", "orb"), note="holding VWAP"),
            FocusEntry(con_id=265598, strategies=("momo",), note="fresh highs"),
            FocusEntry(con_id=777, strategies=("orb",), note="no symbol known"),
        ),
    )
    await insert_plan(
        PlanRecord(
            plan_id=PLAN_0946, session_date=SESSION, slot="09:46", created_at=_utc(13, 50, 10),
            manager_version="manager-v1", prompt_id="day-plan-v1", model="claude-opus-5-5",
            outcome="valid", candidates=(4931, 265598, 777), gates=_gates(), plan=second,
            valid_until=_utc(15, 5, 10), context_digest="f" * 64, input_tokens=7100,
            output_tokens=3900, cost_usd=Decimal("0.21"), latency_ms=7200,
        )
    )


async def _seed_switch_latch_spend() -> None:
    from trading.manager.store import reserve_spend, settle_spend
    from trading.manager.switch import latch_day_mode, set_switch

    await set_switch("off", source="cli", actor="owner (autotraderctl)", reason="not yet", now=_utc(12, 0))
    await set_switch("on", source="telegram", actor="owner (telegram)", reason="trial week", now=_utc(13, 2))
    await latch_day_mode(SESSION, "momo", now=_utc(13, 55))
    budget = Decimal("5")
    for estimate, actual, moment in (("0.5", "0.4123", _utc(13, 20)), ("0.5", "0.21", _utc(13, 50))):
        assert await reserve_spend(
            spend_date=SESSION, purpose="plan", budget_usd=budget,
            estimate_usd=Decimal(estimate), now=moment,
        )
        await settle_spend(
            spend_date=SESSION, purpose="plan", estimate_usd=Decimal(estimate),
            actual_usd=Decimal(actual), now=moment + timedelta(seconds=9),
        )


def _seed_universe(path: Path) -> None:
    stamp = _utc(12, 30).isoformat()
    with sqlite3.connect(path) as db:
        for con_id, symbol, rank, qualified in ((4931, "BF B", 3, 1), (265598, "AAPL", 1, 1), (999, "ETFX", 2, 0)):
            db.execute(
                """INSERT INTO autotrader_manager_day_universe
                   (session_date, con_id, symbol, primary_exchange, first_seen, last_seen,
                    sources_json, best_rank, qualified, reason)
                   VALUES (?, ?, ?, 'NYSE', ?, ?, '["HOT_BY_VOLUME"]', ?, ?, ?)""",
                (TODAY, con_id, symbol, stamp, stamp, rank, qualified, None if qualified else "stock_type_etf"),
            )
        # The watchlist knows the first under its own spelling; the day universe's wins.
        for symbol, con_id in (("BFB", 4931), ("AAPL", 265598)):
            db.execute(
                "INSERT OR IGNORE INTO autotrader_universe VALUES ('watchlist', ?, 'NYSE', ?, ?, 'NYSE', 'resolved', NULL, ?)",
                (symbol, symbol, con_id, stamp),
            )
        db.commit()


async def _seed_verdicts(path: Path) -> None:
    from trading.manager.store import insert_verdict

    strategy_id = await _shadow_strategy("demo")
    momo_run = f"momo-ap-{strategy_id[:8]}-{TODAY}"
    old_run = f"momo-ap-{strategy_id[:8]}-2026-10-12"
    await _run(run_id=momo_run, owner_id="demo", strategy_id=strategy_id, session_day=SESSION)
    await _run(run_id=old_run, owner_id="demo", strategy_id=strategy_id, session_day=date(2026, 10, 12))
    for rank, (con_id, symbol) in enumerate(((4931, "BF B"), (265598, "AAPL")), start=1):
        await CandidateRepository().save(
            Candidate(
                id=f"{momo_run}:{con_id}", scanner_run_id=f"{momo_run}-scan", con_id=con_id,
                symbol=symbol, primary_exchange="NYSE", rank=rank, admission_status="admitted",
            )
        )
    with sqlite3.connect(path) as db:
        (content_hash,) = db.execute(
            "SELECT content_hash FROM autotrader_strategy_versions WHERE strategy_id = ? AND version = 1",
            (strategy_id,),
        ).fetchone()
        for fingerprint, con_id, symbol, detected, entry, stop, target in (
            (FP_A, 4931, "BF B", _utc(13, 57, 30), "42.10", "41.60", "43.10"),
            (FP_B, 265598, "AAPL", _utc(14, 0, 30), "230.5", "229", "233.5"),
        ):
            db.execute(
                """INSERT INTO autotrader_manager_setups
                   (run_id, setup_fingerprint, candidate_id, strategy_id, strategy_version,
                    strategy_name, strategy_kind, spec_fingerprint, symbol, con_id, session_date,
                    bar_ts, detected_at, hour_bucket, entry_price, stop_price, target_price, atr,
                    levels_json, setup_json, created_at)
                   VALUES (?, ?, ?, ?, 1, 'AP_MOMO_HOD_V1', 'momo', ?, ?, ?, ?, ?, ?, 9, ?, ?, ?,
                           '0.35', '{}', '{}', ?)""",
                (
                    momo_run, fingerprint, f"{momo_run}:{con_id}", strategy_id, content_hash,
                    symbol, con_id, TODAY, (detected - timedelta(minutes=5)).isoformat(),
                    detected.isoformat(), entry, stop, target, (detected + timedelta(seconds=30)).isoformat(),
                ),
            )
        db.commit()
    await insert_verdict(
        run_id=momo_run, setup_fingerprint=FP_A, plan_id=PLAN_0946, mode="managed",
        step=Decimal("0.5"), note="Tight stop; half size", created_at=_utc(13, 58),
    )
    await insert_verdict(
        run_id=momo_run, setup_fingerprint=FP_B, plan_id=PLAN_0946, mode="managed",
        step=Decimal("0"), note="Extended from VWAP", created_at=_utc(14, 1),
    )
    await insert_verdict(
        run_id=momo_run, setup_fingerprint=FP_C, plan_id=None, mode="neutral",
        step=Decimal("1"), note=None, created_at=_utc(14, 3),
    )
    await insert_verdict(
        run_id=old_run, setup_fingerprint=FP_D, plan_id=None, mode="neutral",
        step=Decimal("1"), note=None, created_at=_utc(14, 3, day=date(2026, 10, 12)),
    )


async def _seed_everything(path: Path) -> None:
    from db.core import init_db

    await init_db()
    await _seed_switch_latch_spend()
    await _seed_plans()
    _seed_universe(path)
    await _seed_verdicts(path)


def _notifier_state(directory: Path, *, alerted_at: datetime | None, session_day: date = SESSION) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "version": 1,
        "session_date": session_day.isoformat(),
        "delivered": {"started": "sent"},
        "tails": {},
        "alerts": {} if alerted_at is None else {"ibkr_session": alerted_at.isoformat()},
        "market_regime": None,
    }
    (directory / f"{SESSION.isoformat()}.json").write_text(json.dumps(payload), encoding="utf-8")


# --- the contract ---------------------------------------------------------------------------


def test_the_manager_routes_are_five_reads_under_live_and_never_an_autopilot_route():
    from main import app

    spec = app.openapi()["paths"]
    assert all(path in spec for path in MANAGER_PATHS)
    assert all(set(spec[path]) == {"get"} for path in MANAGER_PATHS)
    assert not any(path.lower().startswith("/api/autopilot") for path in spec)
    assert not any("/manager" in path and set(spec[path]) != {"get"} for path in spec)


def test_the_router_shares_the_managers_literals_without_importing_it():
    from routers import live_manager_routes as routes
    from trading.manager import constants
    from trading.manager.report import MANAGED_FAMILY
    from trading.operations.notify_service import state_file
    from trading.operations.paths import UserPaths

    assert routes.MANAGER_DAILY_BUDGET_USD == constants.MANAGER_DAILY_BUDGET_USD
    assert routes.PLAN_SPEND_PURPOSE == constants.PLAN_SPEND_PURPOSE
    assert routes.PLAN_VALID == constants.PLAN_VALID
    assert routes.PLAN_MISSING == constants.PLAN_MISSING
    assert routes.PLAN_EXPIRED == constants.PLAN_EXPIRED
    assert routes.MANAGED_FAMILY == MANAGED_FAMILY
    source = Path(routes.__file__).read_text(encoding="utf-8")
    assert "trading.manager" not in source and "trading.intelligence" not in source
    assert "POST" not in source.replace("no POST", "")
    # The notifier's state layout and its alert key, as the route reads them.
    paths = UserPaths(home=Path("/home/x"))
    assert state_file(paths, SESSION) == paths.data_directory / routes.NOTIFIER_STATE_DIRNAME / f"{TODAY}.json"
    notifier = Path(state_file.__code__.co_filename).read_text(encoding="utf-8")
    assert f'_alert_allowed("{routes.IBKR_SESSION_ALERT_KEY}")' in notifier
    assert routes.COMPETING_LOGIN_ACTIVE_WINDOW > timedelta(minutes=10)  # the notifier's alert interval


@pytest.mark.anyio
async def test_manager_routes_require_a_bearer_token(manager_app) -> None:
    from db.core import init_db

    app, _, _, _ = manager_app
    await init_db()
    async with _client(app) as client:
        for path in MANAGER_PATHS:
            assert (await client.get(path)).status_code == 401, path
            rejected = await client.get(path, headers={"Authorization": "Bearer not-a-token"})
            assert rejected.status_code == 401, path


@pytest.mark.anyio
@pytest.mark.parametrize("version", [13, 15])
async def test_manager_routes_say_the_migration_is_pending_and_never_migrate(manager_app, version) -> None:
    app, database, _, _ = manager_app
    await apply_up_to(database, version)
    await _users_table(database)
    before = database.read_bytes()
    for path in MANAGER_PATHS:
        status, body = await _get(app, path)
        assert status == 200, path
        assert body["available"] is False, path
        assert body["reason"] == "schema_migration_pending", path
        assert f"schema v{version}" in body["detail"] and "16" in body["detail"], body
        assert set(body) == {"available", "reason", "detail"}
    assert database.read_bytes() == before
    with sqlite3.connect(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "autotrader_manager_plans" not in tables


@pytest.mark.anyio
async def test_manager_routes_on_a_fresh_ledger_show_the_switch_off_and_nothing_planned(manager_app) -> None:
    from db.core import init_db

    app, _, _, _ = manager_app
    await init_db()

    status, body = await _get(app, "/api/live/manager/status")
    assert status == 200
    assert body == {
        "available": True,
        "server_time_utc": "2026-10-13T14:05:00.000Z",
        "session_date": TODAY,
        "switch": {"state": "off", "since": None, "source": None, "reason": None},
        "latch": None,
        # Rewritten explicitly for v7c (not weakened): migration 17 (manual-entry-stop, owner
        # decision D5) is now the code's newest, so a fresh ledger is current at v17.
        "migrations": {"applied_version": 17, "code_version": 17, "current": True},
        "plans": {"attempts": 0, "valid": 0, "in_force": None},
        "spend": {
            "spend_date": TODAY, "purpose": "plan", "calls": 0, "spent_usd": "0",
            "reserved_usd": "0", "committed_usd": "0", "budget_usd": "5", "remaining_usd": "5",
            "used_pct": 0.0, "exhausted": False,
        },
        "day_universe": {"qualified": 0, "seen": 0},
        "competing_login": {"active": False, "last_alert_at": None},
    }
    _, plan = await _get(app, "/api/live/manager/plan")
    assert plan == {
        "available": True, "session_date": TODAY, "in_force": False, "plan": None,
        "last_attempt": None, "neutral_reason": "plan_missing",
    }
    _, plans = await _get(app, "/api/live/manager/plans")
    assert plans == {"available": True, "session_date": TODAY, "plans": []}
    _, verdicts = await _get(app, "/api/live/manager/verdicts")
    assert verdicts == {"available": True, "session_date": TODAY, "verdicts": []}
    _, spend = await _get(app, "/api/live/manager/spend")
    assert spend == {"available": True, **body["spend"]}


@pytest.mark.anyio
async def test_a_managed_morning_is_shown_in_full(manager_app) -> None:
    app, database, _, notify_dir = manager_app
    await _seed_everything(database)
    _notifier_state(notify_dir, alerted_at=NOW - timedelta(minutes=4))

    status, body = await _get(app, "/api/live/manager/status")
    assert status == 200
    assert body["switch"] == {
        "state": "on", "since": "2026-10-13T13:02:00.000Z", "source": "telegram", "reason": "trial week",
    }
    assert body["latch"] == {
        "family": "momo", "mode": "managed", "managed": True, "latched_at": "2026-10-13T13:55:00.000Z",
    }
    assert body["plans"] == {"attempts": 3, "valid": 2, "in_force": PLAN_0946}
    assert body["spend"] == {
        "spend_date": TODAY, "purpose": "plan", "calls": 2, "spent_usd": "0.6223",
        "reserved_usd": "0", "committed_usd": "0.6223", "budget_usd": "5",
        "remaining_usd": "4.3777", "used_pct": 12.4, "exhausted": False,
    }
    assert body["day_universe"] == {"qualified": 2, "seen": 3}
    assert body["competing_login"] == {"active": True, "last_alert_at": "2026-10-13T14:01:00.000Z"}
    # No identifiers: who flipped the switch stays in the ledger.
    assert "actor" not in json.dumps(body) and "owner" not in json.dumps(body)

    _, current = await _get(app, "/api/live/manager/plan")
    assert current["in_force"] is True and current["last_attempt"] is None
    assert current["neutral_reason"] is None
    plan = current["plan"]
    assert plan["plan_id"] == PLAN_0946 and plan["slot"] == "09:46" and plan["attempt"] == 2
    assert plan["regime"] == "range" and plan["valid"] is True and plan["in_force"] is True
    assert plan["created_at"] == "2026-10-13T13:50:10.000Z"
    assert plan["valid_until"] == "2026-10-13T15:05:10.000Z"
    assert plan["strategies"] == [
        {"strategy": "momo", "step": "0.5", "on": True, "reason": "choppy tape"},
        {"strategy": "orb", "step": "1", "on": True, "reason": "clean ranges"},
    ]
    assert plan["focus"] == [
        {"con_id": 4931, "symbol": "BF B", "strategies": ["momo", "orb"], "note": "holding VWAP"},
        {"con_id": 265598, "symbol": "AAPL", "strategies": ["momo"], "note": "fresh highs"},
        {"con_id": 777, "symbol": None, "strategies": ["orb"], "note": "no symbol known"},
    ]
    assert plan["focus_count"] == 3 and plan["dropped"] == [] and plan["dropped_count"] == 0
    assert plan["gates"] == {"kill_switch": False, "day_stop": False, "risk_off": False}
    assert (plan["cost_usd"], plan["latency_ms"], plan["input_tokens"], plan["output_tokens"]) == (
        "0.21", 7200, 7100, 3900
    )
    assert (plan["model"], plan["manager_version"]) == ("claude-opus-5-5", "manager-v1")

    _, history = await _get(app, "/api/live/manager/plans")
    rows = history["plans"]
    assert [(row["plan_id"], row["attempt"], row["outcome"], row["in_force"]) for row in rows] == [
        (PLAN_0946, 2, "valid", True),
        (PLAN_0946_FAILED, 1, "ai_timeout", False),
        (PLAN_0920, 1, "valid", False),  # live window, but superseded by the newer plan
    ]
    failed = rows[1]
    assert failed["valid"] is False and failed["regime"] is None and failed["focus_count"] is None
    assert (failed["latency_ms"], failed["cost_usd"], failed["input_tokens"]) == (90000, "0", None)
    assert failed["valid_until"] is None
    first = rows[2]
    assert (first["regime"], first["focus_count"], first["dropped_count"]) == ("trend_up", 1, 1)
    assert first["cost_usd"] == "0.4123" and first["valid_until"] == "2026-10-13T14:35:40.000Z"
    assert "strategies" not in first and "focus" not in first

    _, verdicts = await _get(app, "/api/live/manager/verdicts")
    listed = verdicts["verdicts"]
    assert [item["id"] for item in listed] == [3, 2, 1]
    assert listed[2] == {
        "id": 1, "created_at": "2026-10-13T13:58:00.000Z", "mode": "managed", "step": "0.5",
        "skipped": False, "note": "Tight stop; half size", "plan_id": PLAN_0946,
        "symbol": "BF B", "strategy": "AP_MOMO_HOD_V1", "detected_at": "2026-10-13T13:57:30.000Z",
        "entry_price": "42.1", "stop_price": "41.6", "target_price": "43.1",
    }
    assert listed[1]["skipped"] is True and listed[1]["step"] == "0"
    assert (listed[1]["symbol"], listed[1]["note"]) == ("AAPL", "Extended from VWAP")
    # A neutral verdict has no plan and, here, no setup row yet: never a wrong symbol.
    assert listed[0] == {
        "id": 3, "created_at": "2026-10-13T14:03:00.000Z", "mode": "neutral", "step": "1",
        "skipped": False, "note": None, "plan_id": None, "symbol": None, "strategy": None,
        "detected_at": None, "entry_price": None, "stop_price": None, "target_price": None,
    }
    _, limited = await _get(app, "/api/live/manager/verdicts?limit=2")
    assert [item["id"] for item in limited["verdicts"]] == [3, 2]
    async with _client(app) as client:
        assert (await client.get("/api/live/manager/verdicts?limit=0", headers=_bearer())).status_code == 422

    _, spend = await _get(app, "/api/live/manager/spend")
    assert spend == {"available": True, **body["spend"]}


@pytest.mark.anyio
async def test_an_expired_plan_leaves_managed_strategies_neutral_and_names_the_last_attempt(manager_app) -> None:
    app, database, clock, _ = manager_app
    await _seed_everything(database)
    clock.value = _utc(15, 30)  # 11:30 ET, past the 09:46 plan's 75 minutes

    _, current = await _get(app, "/api/live/manager/plan")
    assert current["in_force"] is False and current["plan"] is None
    assert current["neutral_reason"] == "plan_expired"
    assert current["last_attempt"]["plan_id"] == PLAN_0946
    assert current["last_attempt"]["in_force"] is False
    _, body = await _get(app, "/api/live/manager/status")
    assert body["plans"] == {"attempts": 3, "valid": 2, "in_force": None}
    _, history = await _get(app, "/api/live/manager/plans")
    assert all(row["in_force"] is False for row in history["plans"])

    clock.value = _utc(13, 0)  # 09:00 ET, before any plan was written
    _, early = await _get(app, "/api/live/manager/plan")
    assert early == {
        "available": True, "session_date": TODAY, "in_force": False, "plan": None,
        "last_attempt": None, "neutral_reason": "plan_missing",
    }


@pytest.mark.anyio
async def test_the_competing_login_banner_follows_the_notifiers_state_file(manager_app) -> None:
    from db.core import init_db

    app, _, _, notify_dir = manager_app
    await init_db()

    async def login() -> dict:
        _, body = await _get(app, "/api/live/manager/status")
        return body["competing_login"]

    assert await login() == {"active": False, "last_alert_at": None}
    _notifier_state(notify_dir, alerted_at=NOW - timedelta(minutes=14, seconds=59))
    assert await login() == {"active": True, "last_alert_at": "2026-10-13T13:50:01.000Z"}
    _notifier_state(notify_dir, alerted_at=NOW - timedelta(minutes=15))
    assert await login() == {"active": False, "last_alert_at": "2026-10-13T13:50:00.000Z"}
    # A clock skew into the future is not an active alert either.
    _notifier_state(notify_dir, alerted_at=NOW + timedelta(minutes=1))
    assert (await login())["active"] is False
    # Another session's file, a torn write and no file at all read as no alert.
    _notifier_state(notify_dir, alerted_at=NOW, session_day=date(2026, 10, 12))
    assert await login() == {"active": False, "last_alert_at": None}
    (notify_dir / f"{TODAY}.json").write_text("{\"version\": 1, \"session_date\":", encoding="utf-8")
    assert await login() == {"active": False, "last_alert_at": None}
    (notify_dir / f"{TODAY}.json").unlink()
    assert await login() == {"active": False, "last_alert_at": None}


@pytest.mark.anyio
async def test_polled_manager_queries_stay_on_indexes(manager_app) -> None:
    from db.core import get_db, init_db
    from routers.live_manager_routes import (
        _DAY_LATCH,
        _DAY_PLANS,
        _DAY_SPEND,
        _DAY_UNIVERSE_COUNTS,
        _DAY_VERDICTS,
        _LATEST_SWITCH_EVENT,
    )

    _, database, _, _ = manager_app
    await init_db()
    queries = {
        "plans": (_DAY_PLANS, (TODAY,)),
        "spend": (_DAY_SPEND, (TODAY, "plan")),
        "latch": (_DAY_LATCH, (TODAY, "momo")),
        "universe": (_DAY_UNIVERSE_COUNTS, (TODAY,)),
        "switch": (_LATEST_SWITCH_EVENT, ()),
        "verdicts": (_DAY_VERDICTS, (TODAY, 20)),
    }
    plans: dict[str, str] = {}
    async with get_db() as db:
        for name, (sql, parameters) in queries.items():
            async with db.execute(f"EXPLAIN QUERY PLAN {sql}", parameters) as cursor:
                plans[name] = " | ".join(str(row[-1]) for row in await cursor.fetchall())
    assert "idx_autotrader_manager_plans_session" in plans["plans"], plans["plans"]
    assert "idx_autotrader_manager_day_universe_rank" in plans["universe"], plans["universe"]
    for name in ("spend", "latch"):
        assert "PRIMARY KEY" in plans[name] or "sqlite_autoindex" in plans[name], plans[name]
    assert "TEMP B-TREE" not in plans["switch"], plans["switch"]
    assert "SEARCH r USING" in plans["verdicts"] and "SEARCH s USING" in plans["verdicts"], plans["verdicts"]
    assert "TEMP B-TREE" not in plans["verdicts"], plans["verdicts"]
