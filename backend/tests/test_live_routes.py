"""Read-only live dashboard routes: status, screener, signals, and PAPER bridge orders."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from itertools import count
import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest

from trading.contracts import (
    AIReview,
    Candidate,
    Decision,
    DecisionOutcome,
    EngineMode,
    FixedUniverse,
    RiskApproval,
    RiskPolicy,
    RuleCondition,
    Run,
    ScannerRun,
    StrategySpec,
    StrategyState,
    TradeIntent,
    fingerprint_payload,
)
from trading.data import CandidateRepository, RunRepository, ScannerRunRepository
from trading.storage.repository import create_strategy, record_decision, transition_strategy


NOW = datetime(2026, 10, 12, 14, 5, tzinfo=timezone.utc)  # Monday 10:05 New York
TODAY = "2026-10-12"
DECIDED = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
SETUP = "a" * 64
LIVE_PATHS = ("/api/live/status", "/api/live/screener", "/api/live/signals", "/api/live/orders")


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def live_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[object, Path, _Clock]]:
    from config import cfg
    from main import app
    from routers.live_routes import bridge_state_path, live_clock

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "live_routes.db"))
    state_file = tmp_path / "paper_bridge_state.json"
    clock = _Clock(NOW)
    app.dependency_overrides[live_clock] = lambda: clock
    app.dependency_overrides[bridge_state_path] = lambda: state_file
    try:
        yield app, state_file, clock
    finally:
        app.dependency_overrides.pop(live_clock, None)
        app.dependency_overrides.pop(bridge_state_path, None)


_CLIENT_HOSTS = count(1)


def _client(app: object) -> AsyncClient:
    # The shared app keeps one rate-limit bucket per client IP for the whole
    # session, and its auth limit counts every request from that IP. A unique
    # loopback address per client keeps these tests from exhausting the bucket
    # that other modules' /api/auth/token calls use.
    host = f"127.0.42.{next(_CLIENT_HOSTS)}"
    return AsyncClient(
        transport=ASGITransport(app=app, client=(host, 50000)), base_url="http://test"
    )


def _bearer() -> dict[str, str]:
    from auth import create_token

    return {"Authorization": f"Bearer {create_token('demo')}"}


async def _shadow_strategy(owner_id: str) -> str:
    spec = StrategySpec(
        name="Live monitor fixture",
        universe=FixedUniverse(symbols=("MMED",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale="Live dashboard route fixture.",
    )
    record = await create_strategy(spec=spec, owner_id=owner_id, actor=owner_id)
    for revision, target in enumerate((StrategyState.BACKTESTING, StrategyState.SHADOW)):
        record = await transition_strategy(
            strategy_id=record.strategy_id,
            version=1,
            owner_id=owner_id,
            expected_revision=revision,
            target=target,
            actor=owner_id,
            reason="fixture",
        )
    return record.strategy_id


async def _run(*, run_id: str, owner_id: str, strategy_id: str, session_day: date) -> str:
    await RunRepository().save(
        Run(
            id=run_id,
            owner_id=owner_id,
            source_sha="abcdef1",
            strategy_id=strategy_id,
            strategy_version=1,
            policy_fingerprints={"risk": RiskPolicy().fingerprint()},
            session_date=session_day,
            mode=EngineMode.SHADOW,
        )
    )
    parameters = {"scan_code": "HOT_BY_VOLUME", "limit": 50}
    scanner = ScannerRun(
        id=f"{run_id}-scan",
        run_id=run_id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=DECIDED - timedelta(minutes=30),
    )
    await ScannerRunRepository().save(scanner)
    return scanner.id


def _review(decision: str, rationale: str) -> AIReview:
    return AIReview(
        setup_fingerprint=SETUP,
        decision=decision,
        confidence="0.66",
        rationale=rationale,
        model_id="fixture-model",
        prompt_version="fixture-prompt",
        response_hash="c" * 64,
        latency_ms=5,
        cost_usd="0.01",
    )


def _risk(
    review: AIReview, *, strategy_id: str, symbol: str, codes: tuple[str, ...] = ()
) -> RiskApproval:
    expires = DECIDED + timedelta(seconds=15)
    intent = None
    if not codes:
        intent = TradeIntent(
            execution_key=f"exec-{symbol}",
            setup_fingerprint=SETUP,
            approval_token="t" * 32,
            expires_at=expires,
            strategy_id=strategy_id,
            strategy_version=1,
            symbol=symbol,
            quantity=5266,
            entry_limit="19.11",
            stop_price="18.94",
            target_price="19.45",
        )
    return RiskApproval(
        approved=intent is not None,
        codes=codes,
        intent=intent,
        setup_fingerprint=SETUP,
        ai_review_fingerprint=review.fingerprint(),
        policy_fingerprint="d" * 64,
        portfolio_fingerprint="e" * 64,
        expires_at=expires,
    )


async def _decide(
    *,
    run_id: str,
    owner_id: str,
    strategy_id: str,
    symbol: str,
    rank: int,
    outcome: DecisionOutcome,
    created_at: datetime,
    review: AIReview | None = None,
    risk: RiskApproval | None = None,
) -> str:
    candidate = Candidate(
        id=f"{run_id}-{symbol}",
        scanner_run_id=f"{run_id}-scan",
        con_id=1000 + rank,
        symbol=symbol,
        primary_exchange="NYSE" if " " in symbol else "NASDAQ",
        rank=rank,
        admission_status="admitted",
    )
    await CandidateRepository().save(candidate)
    decision = Decision(
        id=f"decision-{run_id}-{symbol}",
        run_id=run_id,
        candidate_id=candidate.id,
        strategy_id=strategy_id,
        strategy_version=1,
        decision_key=sha256(f"{run_id}:{symbol}".encode()).hexdigest(),
        outcome=outcome,
        setup_fingerprint=SETUP if review is not None else None,
        ai_review=review,
        risk=risk,
        created_at=created_at,
    )
    await record_decision(decision=decision, owner_id=owner_id)
    return decision.id


async def _seed_ledger() -> dict[str, str]:
    from db.core import init_db

    await init_db()
    strategy_id = await _shadow_strategy("demo")
    orb_run = f"shadow-{strategy_id[:8]}-{TODAY}"
    momo_run = f"momo-{strategy_id[:8]}-{TODAY}"
    old_run = f"shadow-{strategy_id[:8]}-2026-10-09"
    for run_id, day in ((orb_run, date(2026, 10, 12)), (momo_run, date(2026, 10, 12)), (old_run, date(2026, 10, 9))):
        await _run(run_id=run_id, owner_id="demo", strategy_id=strategy_id, session_day=day)

    common = {"owner_id": "demo", "strategy_id": strategy_id}
    accepted = _review("PROPOSE", "Clean opening-range breakout.")
    abstained = _review("ABSTAIN", "Extended move into resistance " * 12)
    blocked = _review("PROPOSE", "Valid setup.")
    ids = {
        "accepted": await _decide(
            run_id=orb_run, symbol="MMED", rank=1, outcome=DecisionOutcome.SHADOW_ACCEPTED,
            created_at=DECIDED + timedelta(seconds=20), review=accepted,
            risk=_risk(accepted, strategy_id=strategy_id, symbol="MMED"), **common,
        ),
        "rule": await _decide(
            run_id=orb_run, symbol="AKA", rank=2, outcome=DecisionOutcome.RULE_REJECTED,
            created_at=DECIDED + timedelta(seconds=30), **common,
        ),
        "data": await _decide(
            run_id=orb_run, symbol="WFF", rank=3, outcome=DecisionOutcome.DATA_BLOCKED,
            created_at=DECIDED + timedelta(seconds=40), **common,
        ),
        "abstained": await _decide(
            run_id=momo_run, symbol="NVDA", rank=1, outcome=DecisionOutcome.AI_ABSTAINED,
            created_at=DECIDED + timedelta(seconds=60), review=abstained, **common,
        ),
        "risk": await _decide(
            run_id=momo_run, symbol="BRK B", rank=2, outcome=DecisionOutcome.RISK_BLOCKED,
            created_at=DECIDED + timedelta(seconds=90), review=blocked,
            risk=_risk(
                blocked, strategy_id=strategy_id, symbol="BRK B",
                codes=("max_open_positions", "daily_loss_limit"),
            ),
            **common,
        ),
        "old": await _decide(
            run_id=old_run, symbol="OLD", rank=1, outcome=DecisionOutcome.RULE_REJECTED,
            created_at=datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc), **common,
        ),
    }

    foreign_strategy = await _shadow_strategy("owner-b")
    foreign_run = f"shadow-{foreign_strategy[:8]}-{TODAY}"
    await _run(
        run_id=foreign_run, owner_id="owner-b", strategy_id=foreign_strategy,
        session_day=date(2026, 10, 12),
    )
    ids["foreign"] = await _decide(
        run_id=foreign_run, owner_id="owner-b", strategy_id=foreign_strategy, symbol="TSLA",
        rank=1, outcome=DecisionOutcome.RULE_REJECTED, created_at=DECIDED + timedelta(seconds=99),
    )
    return ids


async def _insert_snapshot(snapshot_id: str, session_day: str, observed_at: str, rows: list[dict]) -> None:
    from db.core import transaction

    payload = {
        "schema": "screener.v1",
        "session_date": session_day,
        "observed_at": observed_at,
        "sweep_seconds": 12.3,
        "universe_size": 503,
        "qualified": 410,
        "quoted": 400,
        "errors": 10,
        "rows": rows,
    }
    async with transaction() as db:
        await db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?, ?, ?, ?)",
            (snapshot_id, session_day, observed_at, json.dumps(payload)),
        )


def _row(symbol: str, score: str, *, hot: bool = False, hot_rank: int | None = None) -> dict:
    return {
        "symbol": symbol, "con_id": 4815747, "primary_exchange": "NASDAQ", "source": "watchlist",
        "last": "123.45", "change_pct": "2.88", "gap_pct": "0.83", "rvol": "2.40",
        "volume_shares": "12345600", "pct_from_high": "-0.44", "score": score,
        "hot": hot, "hot_rank": hot_rank,
    }


@pytest.mark.anyio
async def test_live_routes_require_a_bearer_token(live_app) -> None:
    from db.core import init_db

    app, _, _ = live_app
    await init_db()
    async with _client(app) as client:
        for path in LIVE_PATHS:
            response = await client.get(path)
            assert response.status_code == 401, path
            rejected = await client.get(path, headers={"Authorization": "Bearer not-a-token"})
            assert rejected.status_code == 401, path


@pytest.mark.anyio
async def test_bootstrap_token_unlocks_live_status(live_app, monkeypatch) -> None:
    from config import cfg
    from db.core import init_db

    app, _, _ = live_app
    monkeypatch.setattr(cfg, "JWT_BOOTSTRAP_SECRET_FILE", "")
    await init_db()
    async with _client(app) as client:
        issued = await client.post(
            "/api/auth/token", headers={"X-Bootstrap-Secret": cfg.JWT_BOOTSTRAP_SECRET}
        )
        assert issued.status_code == 200
        bearer = {"Authorization": f"Bearer {issued.json()['access_token']}"}
        status = await client.get("/api/live/status", headers=bearer)
    assert status.status_code == 200
    assert status.json()["session_date"] == TODAY


@pytest.mark.anyio
async def test_signals_explain_each_outcome_newest_first(live_app) -> None:
    app, _, _ = live_app
    ids = await _seed_ledger()
    async with _client(app) as client:
        response = await client.get("/api/live/signals", headers=_bearer())
        older = await client.get("/api/live/signals?date=2026-10-09", headers=_bearer())
        invalid = await client.get("/api/live/signals?date=yesterday", headers=_bearer())
        limited = await client.get("/api/live/signals?limit=2", headers=_bearer())

    assert response.status_code == 200
    signals = response.json()
    # Newest first, today only, and never another owner's decisions.
    assert [item["id"] for item in signals] == [
        ids["risk"], ids["abstained"], ids["data"], ids["rule"], ids["accepted"]
    ]
    by_id = {item["id"]: item for item in signals}

    accepted = by_id[ids["accepted"]]
    assert accepted["strategy"] == "orb"
    assert accepted["symbol"] == "MMED"
    assert accepted["primary_exchange"] == "NASDAQ"
    assert accepted["outcome"] == "shadow_accepted"
    assert accepted["reason"] == "Would buy: 5266 @ 19.11"
    assert accepted["intent"] == {
        "quantity": 5266, "entry_limit": "19.11", "stop_price": "18.94", "target_price": "19.45"
    }
    assert accepted["ai"] == {
        "decision": "PROPOSE", "confidence": "0.66", "rationale": "Clean opening-range breakout."
    }
    assert accepted["risk_codes"] == []
    assert accepted["created_at"] == "2026-10-12T14:00:20.000Z"

    abstained = by_id[ids["abstained"]]
    assert abstained["strategy"] == "momo"
    assert abstained["intent"] is None
    assert abstained["reason"].startswith("Claude abstained: Extended move into resistance")
    preview = abstained["reason"].removeprefix("Claude abstained: ")
    assert preview.endswith("…") and len(preview.rstrip("…")) <= 160

    risk = by_id[ids["risk"]]
    assert risk["symbol"] == "BRK B"
    assert risk["primary_exchange"] == "NYSE"
    assert risk["risk_codes"] == ["max_open_positions", "daily_loss_limit"]
    assert risk["reason"] == "Risk blocked: max_open_positions, daily_loss_limit"
    assert risk["intent"] is None

    assert by_id[ids["rule"]]["reason"] == "Rules: no qualifying setup (price/liquidity/no breakout)"
    assert by_id[ids["rule"]]["ai"] is None
    assert by_id[ids["data"]]["reason"] == "Market data unavailable in time"

    assert older.status_code == 200
    assert [item["id"] for item in older.json()] == [ids["old"]]
    assert invalid.status_code == 422
    assert [item["id"] for item in limited.json()] == [ids["risk"], ids["abstained"]]


@pytest.mark.anyio
async def test_screener_prefers_today_then_flags_stale_then_reports_empty(live_app) -> None:
    from db.core import init_db

    app, _, _ = live_app
    await init_db()
    async with _client(app) as client:
        empty = await client.get("/api/live/screener", headers=_bearer())
        await _insert_snapshot(
            "friday", "2026-10-09", "2026-10-09T19:59:00+00:00", [_row("AAPL", "0.5000")]
        )
        stale = await client.get("/api/live/screener", headers=_bearer())
        await _insert_snapshot(
            "today-1", TODAY, "2026-10-12T14:03:00+00:00", [_row("MSFT", "0.4000")]
        )
        await _insert_snapshot(
            "today-2",
            TODAY,
            "2026-10-12T14:03:55+00:00",
            [_row("NVDA", "0.8712", hot=True, hot_rank=1), _row("AMD", "0.6000")],
        )
        fresh = await client.get("/api/live/screener", headers=_bearer())

    assert empty.status_code == 200
    assert empty.json() == {
        "schema": "screener.v1", "rows": [], "empty": True, "stale": False,
        "snapshot_age_seconds": None,
    }

    assert stale.json()["stale"] is True
    assert stale.json()["empty"] is False
    assert stale.json()["session_date"] == "2026-10-09"
    assert [row["symbol"] for row in stale.json()["rows"]] == ["AAPL"]

    body = fresh.json()
    assert body["stale"] is False
    assert body["snapshot_id"] == "today-2"
    assert body["snapshot_age_seconds"] == 65.0
    assert body["schema"] == "screener.v1"
    assert body["universe_size"] == 503
    assert [row["symbol"] for row in body["rows"]] == ["NVDA", "AMD"]
    assert body["rows"][0]["hot_rank"] == 1
    assert body["rows"][0]["score"] == "0.8712"


def _bracket(decision_id: str, **fields: object) -> dict[str, object]:
    """A bridge v2 state entry (``asdict(Bracket)`` in scripts_local/paper_bridge.py)."""
    entry: dict[str, object] = {
        "decision_id": decision_id, "symbol": "MMED", "con_id": 1001, "primary_exchange": "NASDAQ",
        "strategy": "orb", "qty": 5266, "entry": "19.11", "stop": "18.94", "target": "19.45",
        "parent": None, "target_id": None, "stop_id": None, "sent_at": "", "skipped": False,
        "skip_reason": "", "status": "pending", "filled": 0, "avg_entry": "0", "exit_filled": 0,
        "avg_exit": "0", "exit_reason": "", "realized_pnl": "0", "parent_cancel_requested": False,
        "resized": False, "flatten_sent": False,
    }
    entry.update(fields)
    return entry


@pytest.mark.anyio
async def test_orders_read_legacy_bridge_state(live_app) -> None:
    app, state_file, _ = live_app
    ids = await _seed_ledger()
    async with _client(app) as client:
        missing = await client.get("/api/live/orders", headers=_bearer())
        state_file.write_text(
            json.dumps(
                {
                    "submitted": {
                        ids["accepted"]: {"parent": 101, "symbol": "MMED"},
                        ids["rule"]: {"parent": None, "skipped": True},
                        "decision-not-in-ledger": {"parent": None},
                    }
                }
            ),
            encoding="utf-8",
        )
        listed = await client.get("/api/live/orders", headers=_bearer())
        state_file.write_text('{"submitted": {"torn', encoding="utf-8")
        torn = await client.get("/api/live/orders", headers=_bearer())

    assert missing.status_code == 200
    assert missing.json() == []

    assert listed.status_code == 200
    orders = listed.json()
    assert [order["decision_id"] for order in orders] == [
        ids["rule"], ids["accepted"], "decision-not-in-ledger"
    ]
    skipped, sent, unknown = orders
    assert sent["status"] == "sent"
    assert sent["parent_order_id"] == 101
    assert sent["symbol"] == "MMED"
    assert sent["strategy"] == "orb"
    assert sent["intent"]["entry_limit"] == "19.11"
    assert sent["decided_at"] == "2026-10-12T14:00:20.000Z"
    assert sent["realized_pnl"] is None
    # The bridge omits the symbol for skipped decisions; the ledger supplies it.
    assert skipped["status"] == "skipped"
    assert skipped["symbol"] == "AKA"
    assert skipped["parent_order_id"] is None
    # Without a parent order id an entry is never reported as sent.
    assert unknown["status"] == "unknown"
    assert unknown["symbol"] is None and unknown["decided_at"] is None

    assert torn.status_code == 503
    assert "not valid JSON" in torn.json()["detail"]


@pytest.mark.anyio
async def test_orders_surface_the_bridge_v2_bracket_lifecycle(live_app) -> None:
    app, state_file, _ = live_app
    ids = await _seed_ledger()
    state_file.write_text(
        json.dumps(
            {
                "submitted": {
                    ids["accepted"]: _bracket(
                        ids["accepted"], parent=101, target_id=102, stop_id=103,
                        sent_at="2026-10-12T14:00:21.123456+00:00", status="closed",
                        filled=5266, avg_entry="19.1100", exit_filled=5266, avg_exit="19.4500",
                        exit_reason="target", realized_pnl="1790.44",
                    ),
                    ids["risk"]: _bracket(
                        ids["risk"], symbol="?", primary_exchange="", strategy="momo", qty=0,
                        entry="0", stop="0", target="0", skipped=True, status="skipped",
                        skip_reason="legacy entry",
                    ),
                    ids["abstained"]: _bracket(ids["abstained"], status="open", parent=None),
                }
            }
        ),
        encoding="utf-8",
    )
    async with _client(app) as client:
        response = await client.get("/api/live/orders", headers=_bearer())

    assert response.status_code == 200
    by_id = {order["decision_id"]: order for order in response.json()}
    closed = by_id[ids["accepted"]]
    assert closed["status"] == "closed"
    assert closed["sent_at"] == "2026-10-12T14:00:21.123Z"
    assert closed["filled_quantity"] == 5266
    assert closed["avg_entry_price"] == "19.1100"
    assert closed["exit_quantity"] == 5266
    assert closed["avg_exit_price"] == "19.4500"
    assert closed["exit_reason"] == "target"
    assert closed["realized_pnl"] == "1790.44"
    assert closed["intent"] == {
        "quantity": 5266, "entry_limit": "19.11", "stop_price": "18.94", "target_price": "19.45"
    }
    # A "?" placeholder symbol and empty venue fall back to the ledger.
    skipped = by_id[ids["risk"]]
    assert skipped["status"] == "skipped"
    assert skipped["symbol"] == "BRK B"
    assert skipped["primary_exchange"] == "NYSE"
    assert skipped["skip_reason"] == "legacy entry"
    assert skipped["intent"] is None
    assert skipped["avg_entry_price"] is None
    # A lifecycle claim without a broker parent order id is not trusted.
    assert by_id[ids["abstained"]]["status"] == "unknown"


@pytest.mark.anyio
async def test_status_reports_session_snapshot_age_and_todays_counts(live_app) -> None:
    app, state_file, clock = live_app
    ids = await _seed_ledger()
    await _insert_snapshot("today", TODAY, "2026-10-12T14:02:30+00:00", [_row("NVDA", "0.9")])
    state_file.write_text(
        json.dumps(
            {
                "submitted": {
                    ids["accepted"]: _bracket(
                        ids["accepted"], parent=101, status="open", filled=5266, avg_entry="19.11"
                    ),
                    ids["abstained"]: _bracket(
                        ids["abstained"], parent=201, status="closed", filled=10, exit_filled=10,
                        realized_pnl="-85.25",
                    ),
                    ids["data"]: _bracket(ids["data"], parent=301, status="needs_attention"),
                    ids["rule"]: {"parent": None, "skipped": True},
                    ids["old"]: {"parent": 55, "symbol": "OLD"},
                }
            }
        ),
        encoding="utf-8",
    )
    async with _client(app) as client:
        open_status = await client.get("/api/live/status", headers=_bearer())
        clock.value = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)  # Saturday
        weekend = await client.get("/api/live/status", headers=_bearer())
        clock.value = NOW
        state_file.write_text("", encoding="utf-8")
        torn = await client.get("/api/live/status", headers=_bearer())

    assert open_status.status_code == 200
    body = open_status.json()
    assert body["server_time_utc"] == "2026-10-12T14:05:00.000Z"
    assert body["server_time_new_york"] == "2026-10-12T10:05:00-04:00"
    assert body["session_date"] == TODAY
    assert body["market"] == {
        "session_date": TODAY,
        "phase": "open",
        "is_open": True,
        "opens_at": "2026-10-12T09:30:00-04:00",
        "closes_at": "2026-10-12T16:00:00-04:00",
        "entry_cutoff_at": "2026-10-12T12:00:00-04:00",
        "next_open_at": None,
        "half_day": False,
    }
    assert body["snapshot_age_seconds"] == 150.0
    assert body["snapshot_observed_at"] == "2026-10-12T14:02:30.000Z"
    assert body["decisions_today"] == {
        "ai_abstained": 1, "data_blocked": 1, "risk_blocked": 1,
        "rule_rejected": 1, "shadow_accepted": 1,
    }
    assert body["decisions_total"] == 5
    # The Friday order is in the state file but is not today's.
    assert body["orders_today"] == {
        "sent": 3, "open": 1, "skipped": 1, "needs_attention": 1, "realized_pnl": "-85.25"
    }
    assert body["bridge_state"] == "ok"

    closed = weekend.json()
    assert closed["session_date"] == "2026-10-10"
    assert closed["market"]["phase"] == "no_session"
    assert closed["market"]["is_open"] is False
    assert closed["market"]["next_open_at"] == "2026-10-12T09:30:00-04:00"
    assert closed["decisions_total"] == 0

    # A torn bridge write degrades the status instead of failing it.
    assert torn.status_code == 200
    assert torn.json()["bridge_state"] == "unreadable"
    assert torn.json()["orders_today"] == {
        "sent": 0, "open": 0, "skipped": 0, "needs_attention": 0, "realized_pnl": "0.00"
    }


@pytest.mark.anyio
async def test_polled_queries_stay_on_indexes(live_app) -> None:
    """The dashboard polls every 5 s; a full ledger or snapshot scan would grow without bound."""
    from db.core import get_db, init_db
    from routers.live_routes import _DAY_DECISIONS, _NEWEST_SNAPSHOT_ORDER, _SNAPSHOT_COLUMNS

    await init_db()
    queries = {
        "day_decisions": (f"SELECT d.id {_DAY_DECISIONS} ORDER BY d.created_at DESC LIMIT 5", ("demo", TODAY)),
        "newest_snapshot": (f"{_SNAPSHOT_COLUMNS} {_NEWEST_SNAPSHOT_ORDER}", ()),
        "newest_snapshot_time": (
            f"SELECT observed_at FROM autotrader_screener_snapshots {_NEWEST_SNAPSHOT_ORDER}", ()
        ),
        "todays_snapshot": (
            f"{_SNAPSHOT_COLUMNS} WHERE session_date = ? ORDER BY observed_at DESC LIMIT 1", (TODAY,)
        ),
    }
    plans: dict[str, str] = {}
    async with get_db() as db:
        for name, (sql, parameters) in queries.items():
            async with db.execute(f"EXPLAIN QUERY PLAN {sql}", parameters) as cursor:
                plans[name] = " | ".join(str(row[-1]) for row in await cursor.fetchall())

    day = plans["day_decisions"]
    assert day.startswith("SCAN r"), day
    for alias in ("sr", "c", "d"):
        assert f"SEARCH {alias} USING" in day, day
    for name in ("newest_snapshot", "newest_snapshot_time", "todays_snapshot"):
        assert "INDEX idx_autotrader_screener_snapshots_observed" in plans[name], plans[name]
        assert "TEMP B-TREE" not in plans[name], plans[name]


def test_market_phase_brackets_the_regular_session() -> None:
    from routers.live_routes import project_market

    before = project_market(datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc))
    after = project_market(datetime(2026, 10, 12, 20, 30, tzinfo=timezone.utc))
    assert before.phase == "pre_open"
    assert before.next_open_at == "2026-10-12T09:30:00-04:00"
    assert after.phase == "after_close"
    assert after.next_open_at == "2026-10-13T09:30:00-04:00"
    with pytest.raises(ValueError):
        project_market(datetime(2026, 10, 12, 14, 0))


def test_bridge_state_parser_never_invents_a_sent_order() -> None:
    from routers.live_routes import BridgeStateUnreadable, parse_bridge_state

    entries = parse_bridge_state(
        json.dumps(
            {
                "submitted": {
                    "a": {"parent": True, "symbol": "X"},
                    "b": "garbage",
                    "c": {"parent": 7, "symbol": "  "},
                    "d": {"parent": 8, "status": "open"},
                    "e": {"parent": None, "status": "closed"},
                    "f": {"parent": 9, "status": "teleported"},
                    "g": {"parent": None, "status": "needs_attention"},
                    "h": {"parent": None, "status": "pending", "symbol": "?"},
                }
            }
        )
    )
    assert [(entry.decision_id, entry.status, entry.parent_order_id, entry.symbol) for entry in entries] == [
        ("a", "unknown", None, "X"),
        ("b", "unknown", None, None),
        ("c", "sent", 7, None),
        ("d", "open", 8, None),
        ("e", "unknown", None, None),
        ("f", "unknown", 9, None),
        ("g", "needs_attention", None, None),
        ("h", "pending", None, None),
    ]
    for raw in ("", "[]", '{"submitted": []}'):
        with pytest.raises(BridgeStateUnreadable):
            parse_bridge_state(raw)
