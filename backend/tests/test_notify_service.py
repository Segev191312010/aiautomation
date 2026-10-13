"""Phone notifications: decisions, bridge/supervisor/login lines, daily report, restarts."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import inspect
import io
import json
import logging
from pathlib import Path
import stat
from types import SimpleNamespace

import httpx
import pytest

from trading.operations.keychain import KeychainReadError
from trading.operations.notifier import save_chat_id, telegram_config_file
from trading.operations.notify_service import (
    BRIDGE_FORWARD_KINDS,
    LOGIN_ALERT_TEXT,
    MISSING_FILE_CHECKPOINT,
    FillCoalescer,
    LogTailer,
    NotifyEnvironment,
    bridge_event_text,
    collect_daily_report,
    format_daily_report,
    format_decision,
    ibkr_alert_text,
    ibkr_session_problem,
    load_signal_decisions,
    parse_bridge_line,
    run_notifier,
    send_test_message,
    startup_text,
    state_file,
    supervisor_event_text,
)
from trading.operations.paths import UserPaths
from trading.scheduler.sessions import MARKET_TIMEZONE

TOKEN ="123456789:AAH_test-token-value_0123456789abcdef"
SESSION = date(2026, 10, 13)
CHAT_ID = 4242
APPROVAL_TOKEN = "di9kdH0Cwc6kqTbicUxHZVx5q790PKGhIgj5CF8FKNA"
BF_B_RATIONALE = (
    "Entry 26.77, stop 26.69 (risk 0.08, about 1 ATR of 0.0792) and target 26.93 (2R) are "
    "internally consistent. Entry sits 0.21 above vwap 26.5586."
)
FIGS_RATIONALE = (
    "Entry 17.83 sits 0.636 above vwap 17.1941, about 6.6x the stop distance of 0.10 "
    "(stop 17.73, ATR ~0.097). The entry is stretched far above vwap relative to risk. "
) * 6


def _et(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 13, hour, minute, second, tzinfo=MARKET_TIMEZONE).astimezone(
        timezone.utc
    )


class FakeTime:
    """Wall and monotonic clocks that move only when the code under test sleeps."""

    def __init__(self, start: datetime) -> None:
        self.now = start
        self.mono = 5_000.0
        self._actions: list[tuple[datetime, Callable[[], object]]] = []

    def at(self, moment: datetime, action: Callable[[], object]) -> None:
        self._actions.append((moment, action))
        self._actions.sort(key=lambda item: item[0])

    def clock(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    async def sleep(self, seconds: float) -> None:
        step = max(float(seconds), 0.0)
        self.now += timedelta(seconds=step)
        self.mono += step
        while self._actions and self._actions[0][0] <= self.now:
            _, action = self._actions.pop(0)
            result = action()
            if inspect.isawaitable(result):
                await result
        await asyncio.sleep(0)


class FakeTelegram:
    def __init__(self, replies: list[httpx.Response] | None = None) -> None:
        self.replies = list(replies or [])
        self.updates: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if self.replies:
            return self.replies.pop(0)
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": self.updates})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": "at_bot"}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.calls)}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def sent(self) -> list[str]:
        return [payload["text"] for method, payload in self.calls if method == "sendMessage"]


async def _no_market_context(session_date: date, *, now: datetime, max_age: timedelta) -> None:
    return None


async def _no_hot_list(session_date: date) -> tuple[()]:
    return ()


def _environment(
    paths: UserPaths,
    telegram: FakeTelegram,
    clock: FakeTime,
    *,
    out: io.StringIO | None = None,
    read_token: Callable[[], str] = lambda: TOKEN,
    configure_logging: bool = False,
    read_market_context=_no_market_context,
    read_hot_list=_no_hot_list,
) -> NotifyEnvironment:
    return NotifyEnvironment(
        paths=paths,
        read_token=read_token,
        transport=telegram.transport,
        clock=clock.clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        out=out or io.StringIO(),
        configure_logging=configure_logging,
        read_market_context=read_market_context,
        read_hot_list=read_hot_list,
    )


@pytest.fixture
def restore_shadow_logging():
    shadow = logging.getLogger("autotrader.shadow")
    saved = (list(shadow.handlers), shadow.propagate, shadow.level)
    yield
    for handler in list(shadow.handlers):
        shadow.removeHandler(handler)
        if handler not in saved[0]:
            handler.close()
    for handler in saved[0]:
        shadow.addHandler(handler)
    shadow.propagate = saved[1]
    shadow.setLevel(saved[2])


# --------------------------------------------------------------------------
# Database seeding (mirrors the live payload shapes of 2026-10-12)
# --------------------------------------------------------------------------


async def _use_database(tmp_path: Path, monkeypatch) -> None:
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "notify.db"))
    await init_db()


async def _seed_run(
    run_id: str, strategy_id: str, name: str, *, session_date: date = SESSION, mode: str = "shadow"
) -> None:
    from db.core import get_db

    now = "2026-10-13T12:00:00+00:00"
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO autotrader_strategy_identities VALUES (?, 'owner', ?, NULL)",
            (strategy_id, now),
        )
        await db.execute(
            """INSERT OR IGNORE INTO autotrader_strategy_versions
               (strategy_id, version, content_hash, spec_json, created_at, created_by)
               VALUES (?, 1, ?, ?, ?, 'owner')""",
            (strategy_id, sha256(name.encode()).hexdigest(), json.dumps({"name": name}), now),
        )
        await db.execute(
            """INSERT INTO autotrader_runs
               (id, owner_id, source_sha, strategy_id, strategy_version, session_date, mode,
                status, policy_fingerprints_json)
               VALUES (?, 'owner', 'abcdef1', ?, 1, ?, ?, 'running', '{}')""",
            (run_id, strategy_id, session_date.isoformat(), mode),
        )
        await db.execute(
            """INSERT INTO autotrader_scanner_runs
               (id, run_id, parameters_json, parameters_fingerprint, status, started_at)
               VALUES (?, ?, '{}', ?, 'completed', ?)""",
            (f"scan-{run_id}", run_id, "a" * 64, now),
        )
        await db.commit()


_RANKS: dict[str, int] = {}


async def _add_decision(
    decision_id: str,
    *,
    run_id: str,
    strategy_id: str,
    symbol: str,
    outcome: str,
    created_at: datetime,
    review: dict | None = None,
    risk: dict | None = None,
) -> None:
    from db.core import get_db

    rank = _RANKS[run_id] = _RANKS.get(run_id, 0) + 1
    candidate_id = f"cand-{decision_id}"
    payload = {
        "id": decision_id,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "strategy_id": strategy_id,
        "strategy_version": 1,
        "outcome": outcome,
        "ai_review": review,
        "risk": risk,
        "created_at": created_at.isoformat(),
    }
    async with get_db() as db:
        await db.execute(
            """INSERT INTO autotrader_candidates
               (id, scanner_run_id, con_id, symbol, rank, contract_json, admission_status)
               VALUES (?, ?, ?, ?, ?, '{}', 'admitted')""",
            (candidate_id, f"scan-{run_id}", 1000 + rank, symbol, rank),
        )
        await db.execute(
            """INSERT INTO autotrader_candidate_decisions
               (id, run_id, candidate_id, strategy_id, strategy_version, decision_key, outcome,
                setup_fingerprint, ai_call_id, risk_reservation_id, payload_json, created_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, NULL, NULL, NULL, ?, ?)""",
            (
                decision_id,
                run_id,
                candidate_id,
                strategy_id,
                sha256(decision_id.encode()).hexdigest(),
                outcome,
                json.dumps(payload),
                created_at.isoformat(),
            ),
        )
        await db.commit()


def _review(
    decision: str,
    rationale: str,
    *,
    confidence: str | None = "0.62",
    cost: str = "0.0468",
    failure: str | None = None,
    model: str = "claude-opus-5-5",
) -> dict:
    return {
        "setup_fingerprint": "f" * 64,
        "decision": decision,
        "confidence": confidence,
        "rationale": rationale,
        "model_id": model,
        "prompt_version": "momo-review-v1",
        "response_hash": "e" * 64,
        "latency_ms": 9000,
        "cost_usd": cost,
        "failure_reason": failure,
    }


def _approved_risk() -> dict:
    return {
        "approved": True,
        "codes": [],
        "intent": {
            "approval_token": APPROVAL_TOKEN,
            "execution_key": "k" * 64,
            "symbol": "BF B",
            "quantity": 3741,
            "side": "BUY",
            "entry_limit": "26.77",
            "stop_price": "26.69",
            "target_price": "26.93",
        },
    }


async def _seed_day(tmp_path: Path, monkeypatch) -> None:
    await _use_database(tmp_path, monkeypatch)
    await _seed_run("shadow-momo-2026-10-13-wl", "strategy-momo", "MOMO_HOD_V1")
    await _seed_run("shadow-orb-2026-10-13", "strategy-orb", "ORB_V1")
    momo = {"run_id": "shadow-momo-2026-10-13-wl", "strategy_id": "strategy-momo"}
    orb = {"run_id": "shadow-orb-2026-10-13", "strategy_id": "strategy-orb"}
    await _add_decision(
        "65172678-bfb", **momo, symbol="BF B", outcome="shadow_accepted",
        created_at=_et(11, 50, 12), review=_review("PROPOSE", BF_B_RATIONALE), risk=_approved_risk(),
    )
    await _add_decision(
        "6f0187d0-figs", **momo, symbol="FIGS", outcome="ai_abstained",
        created_at=_et(14, 31, 28),
        review=_review("ABSTAIN", FIGS_RATIONALE, confidence="0.7", cost="0.057924"),
    )
    await _add_decision(
        "313f1137-mmed", **orb, symbol="MMED", outcome="risk_blocked",
        created_at=_et(10, 0, 20),
        review=_review("PROPOSE", "Opening range break with room to 2R.", confidence="0.66", cost="0.039436"),
        risk={"approved": False, "codes": ["max_open_positions", "symbol_already_active"], "intent": None},
    )
    await _add_decision(
        "aa11bb22-xyz", **momo, symbol="XYZ", outcome="ai_blocked", created_at=_et(12, 5),
        review=_review(
            "ABSTAIN", "AI review blocked before an execution decision was made.",
            confidence=None, cost="0", failure="provider_unavailable", model="unavailable",
        ),
    )
    await _add_decision(
        "rule-ko", **momo, symbol="KO", outcome="rule_rejected", created_at=_et(10, 30)
    )
    await _add_decision(
        "data-amd", **orb, symbol="AMD", outcome="data_blocked", created_at=_et(11, 0)
    )
    # Neither yesterday's run nor a backtest replay of today is news.
    await _seed_run(
        "shadow-momo-2026-10-12-scan", "strategy-momo", "MOMO_HOD_V1", session_date=date(2026, 10, 12)
    )
    await _add_decision(
        "old-bfb", run_id="shadow-momo-2026-10-12-scan", strategy_id="strategy-momo",
        symbol="OLD", outcome="shadow_accepted", created_at=_et(11, 50) - timedelta(days=1),
        review=_review("PROPOSE", "yesterday"), risk=_approved_risk(),
    )
    await _seed_run("backtest-momo-2026-10-13", "strategy-momo", "MOMO_HOD_V1", mode="backtest")
    await _add_decision(
        "bt-replay", run_id="backtest-momo-2026-10-13", strategy_id="strategy-momo",
        symbol="REPLAY", outcome="shadow_accepted", created_at=_et(11, 0),
        review=_review("PROPOSE", "replay"), risk=_approved_risk(),
    )


def _write_bridge_state(paths: UserPaths, entries: dict, *, legacy: bool = False) -> Path:
    if legacy:
        path = paths.data_directory / "paper_bridge_state.json"
    else:
        path = paths.data_directory / "bridge" / f"{SESSION.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"submitted": entries}), encoding="utf-8")
    return path


def _bracket(symbol: str, status: str, **fields: object) -> dict:
    base: dict[str, object] = {
        "symbol": symbol, "strategy": "momo", "status": status, "qty": 100, "filled": 0,
        "exit_filled": 0, "avg_entry": "0", "avg_exit": "0", "realized_pnl": "0",
        "exit_reason": "", "skipped": status == "skipped", "skip_reason": "",
        "sent_at": "2026-10-13T15:50:13.412409+00:00",
    }
    base.update(fields)
    return base


async def _add_snapshot(observed_at: datetime) -> None:
    from db.core import get_db

    async with get_db() as db:
        await db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            ("snap-1", SESSION.isoformat(), observed_at.isoformat(), json.dumps({"rows": []})),
        )
        await db.commit()


# --------------------------------------------------------------------------
# a. Decisions
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_only_todays_live_decisions_with_a_claude_review_are_signals(tmp_path, monkeypatch):
    await _seed_day(tmp_path, monkeypatch)

    decisions = await load_signal_decisions(SESSION)

    assert [(d.symbol, d.strategy, d.outcome) for d in decisions] == [
        ("MMED", "ORB_V1", "risk_blocked"),
        ("BF B", "MOMO_HOD_V1", "shadow_accepted"),
        ("XYZ", "MOMO_HOD_V1", "ai_blocked"),
        ("FIGS", "MOMO_HOD_V1", "ai_abstained"),
    ]


@pytest.mark.anyio
async def test_decision_messages_read_like_a_trade_ticket(tmp_path, monkeypatch):
    await _seed_day(tmp_path, monkeypatch)
    messages = {d.symbol: format_decision(d) for d in await load_signal_decisions(SESSION)}

    assert messages["BF B"].splitlines() == [
        "🔔 MOMO_HOD_V1 · BF B · WOULD BUY",
        "Buy 3741 @ 26.77 · stop 26.69 · target 26.93",
        "Risk $299.28 (0.08/sh) · reward 2.0R · notional $100,146.57",
        "Claude: PROPOSE · confidence 62%",
        BF_B_RATIONALE,
        "11:50:12 ET · decision 65172678",
    ]
    figs = messages["FIGS"].splitlines()
    assert figs[0] == "🤔 MOMO_HOD_V1 · FIGS · Claude abstained"
    assert figs[1] == "Claude: ABSTAIN · confidence 70%"
    assert len(figs[2]) <= 600 and figs[2].endswith("…")
    assert figs[2].startswith("Entry 17.83 sits 0.636 above vwap")
    mmed = messages["MMED"].splitlines()
    assert mmed[0] == "🛑 ORB_V1 · MMED · risk blocked"
    assert mmed[1] == "Risk codes: max_open_positions, symbol_already_active"
    assert mmed[2] == "Claude: PROPOSE · confidence 66%"
    assert messages["XYZ"].splitlines()[:2] == [
        "🚫 MOMO_HOD_V1 · XYZ · AI blocked",
        "Review failed: provider_unavailable",
    ]
    assert "AI review blocked before" not in messages["XYZ"]
    assert all(APPROVAL_TOKEN not in text for text in messages.values())


# --------------------------------------------------------------------------
# b-d. Bridge, supervisor, and data-log lines
# --------------------------------------------------------------------------


def test_bridge_lines_map_to_phone_messages():
    def text(line: str) -> str | None:
        event = parse_bridge_line(line)
        return bridge_event_text(event) if event else None

    assert text("11:50:13 ET  SENT BF B (momo): BUY 3741 @ 26.77 | stop 26.69 | target 26.93") == (
        "🟢 SENT BF B (momo): BUY 3741 @ 26.77 | stop 26.69 | target 26.93 · 11:50:13 ET"
    )
    assert text("11:50:14 ET  FILL BF B: 3741/3741 sh @ 26.7496 (momo)").startswith("💵 FILL BF B")
    assert text("11:51:44 ET  EXIT BF B: 3741 sh @ 26.6627 via stop | P&L -325.09").startswith("🔴 EXIT")
    assert text("11:59:00 ET  EXIT ABC: 100 sh @ 11.205 via target | P&L 120.50").startswith("🟢 EXIT")
    assert text("11:51:44 ET  CLOSED BF B: P&L -325.09 (stop)").startswith("🏁 CLOSED BF B")
    assert text("12:01:00 ET  CANCEL entry XYZ: not filled in 10 min (filled 0/100)").startswith("↩️ CANCEL")
    assert text("12:01:02 ET  CANCELLED XYZ: entry never filled").startswith("↩️ CANCELLED")
    assert text("15:45:01 ET  FLATTEN ABC: target repriced to 10.95 for 100 sh (bid 11.0)").startswith("🧹")
    assert text("15:50:00 ET  FLAT: no open engine positions") == (
        "✅ FLAT: no open engine positions · 15:50:00 ET"
    )
    assert text("15:50:00 ET  CRITICAL ABC: still open after flatten (100 sh)").startswith("⚠️ CRITICAL")
    assert text("12:00:00 ET  SKIP GHI (momo): daily loss limit reached").startswith("⛔ SKIP GHI")
    assert text("12:00:00 ET  SKIP GHI (momo): PAPER kill switch is active").startswith("⛔ SKIP")
    for quiet in (
        "12:00:00 ET  SKIP DEF (momo): decision 75s old",
        "12:00:00 ET  RESIZE BF B: exits set to the 1200 filled shares",
        "12:00:00 ET  INFO heartbeat",
        "12:00:00 ET  order 2: Filled filled=3741 remaining=0 avg=26.75",
        "Traceback (most recent call last):",
        "",
    ):
        assert text(quiet) is None
    assert parse_bridge_line("11:50:14 ET  FILL BF B: 1/2 sh @ 1 (momo)").symbol == "BF B"
    assert BRIDGE_FORWARD_KINDS == {
        "SENT", "FILL", "EXIT", "CLOSED", "CANCEL", "CANCELLED", "FLATTEN", "FLAT", "CRITICAL"
    }


def test_fill_bursts_coalesce_per_symbol_within_ten_seconds():
    coalescer = FillCoalescer()
    coalescer.add("BF B", "💵 FILL BF B: 100/3741", now=0.0)
    coalescer.add("BF B", "💵 FILL BF B: 2000/3741", now=3.0)
    coalescer.add("MMED", "💵 FILL MMED: 50/50", now=4.0)
    coalescer.add("BF B", "💵 FILL BF B: 3741/3741", now=9.0)

    assert coalescer.due(9.9) == []
    assert coalescer.due(10.0) == ["💵 FILL BF B: 3741/3741 (3 fill updates)"]
    assert coalescer.due(13.9) == []
    assert coalescer.due(14.0) == ["💵 FILL MMED: 50/50"]
    coalescer.add("ABC", "💵 FILL ABC: 1/1", now=20.0)
    assert coalescer.drain() == ["💵 FILL ABC: 1/1"] and coalescer.drain() == []


def test_supervisor_child_exits_and_ibkr_session_problems():
    assert supervisor_event_text("2026-10-13T10:02:11-0400 WARNING CHILD_EXIT momo code=1 restart_in=95") == (
        "⚙️ momo exited (code 1), restarting in 95 s"
    )
    assert supervisor_event_text("CHILD_EXIT bridge code=0 restart_in=95.0") == (
        "⚙️ bridge exited (code 0), restarting in 95 s"
    )
    assert supervisor_event_text("CHILD_EXIT screener code=0 restart_in=none") is None
    assert supervisor_event_text("CHILD_EXIT bridge code=-15 restart_in=none") == (
        "⚙️ bridge exited (code -15), not restarting"
    )
    assert supervisor_event_text("CHILD_START momo pid=123") is None
    refused = (
        "2026-10-13T13:25:00+00:00 LAUNCH_REFUSED bridge code=78"
        " reason=unreviewed sha256=1f856f7ee6be reviewed=000000000000"
    )
    assert supervisor_event_text(refused) == (
        "⛔ bridge NOT started: its code is not the reviewed version, so no orders"
        " will be sent until it is reviewed (supervisor.log)"
    )
    assert supervisor_event_text(
        "LAUNCH_REFUSED bridge code=78 reason=unreadable_script error=FileNotFoundError"
    ) == "⛔ bridge NOT started: unreadable_script (supervisor.log)"

    competing = (
        "ERROR autotrader.shadow.screener IBKR login active elsewhere (phone/web) — log out there;"
        " retrying in 60 s (TWS error 10197: No market data during competing live session)"
    )
    assert ibkr_session_problem(competing) == "competing_login"
    assert ibkr_session_problem("TWS error 162: ... from a different IP address") == "competing_login"
    assert ibkr_session_problem("TWS 900001 10197 No market data") == "competing_login"
    assert ibkr_session_problem("09:41:02 ET  INFO TWS -1 10197 No market data during competing live session") == (
        "competing_login"
    )
    assert ibkr_session_problem("shadow run failed: TWSCompetingSessionError") == "competing_login"
    assert ibkr_session_problem("TWS connection lost during qualification") == "connection_lost"
    assert ibkr_session_problem("TWS connection lost", include_connection_lost=False) is None
    assert ibkr_session_problem("sweep observed volume 10197 shares") is None
    assert ibkr_session_problem("Claude: two competing resistance levels overhead") is None
    assert ibkr_alert_text("competing_login", "screener").splitlines()[0] == LOGIN_ALERT_TEXT
    assert "log out" in ibkr_alert_text("connection_lost", "momo")


def test_log_tailer_handles_missing_files_partial_lines_rotation_and_truncation(tmp_path):
    path = tmp_path / "bridge.log"
    tailer = LogTailer(path)
    tailer.start(None)
    assert tailer.poll() == []

    path.write_text("first\nsecond\npart", encoding="utf-8")  # appears later: read from the top
    assert tailer.poll() == ["first", "second"]
    with path.open("a", encoding="utf-8") as stream:
        stream.write("ial line\n")
    assert tailer.poll() == ["partial line"]

    with path.open("a", encoding="utf-8") as stream:
        stream.write("old tail\n")
    path.rename(tmp_path / "bridge.log.1")
    path.write_text("new head\n" + "x" * 200 + "\n", encoding="utf-8")
    assert tailer.poll() == ["old tail", "new head", "x" * 200]

    path.write_text("after truncate\n", encoding="utf-8")  # truncated in place
    assert tailer.poll() == ["after truncate"]
    tailer.close()


def test_log_tailer_starts_at_the_end_and_resumes_from_its_checkpoint(tmp_path):
    path = tmp_path / "bridge.log"
    path.write_text("from an earlier run\n", encoding="utf-8")
    tailer = LogTailer(path)
    tailer.start(None)
    assert tailer.poll() == []
    with path.open("a", encoding="utf-8") as stream:
        stream.write("today 1\nhalf")
    assert tailer.poll() == ["today 1"]
    checkpoint = tailer.checkpoint()
    tailer.close()

    with path.open("a", encoding="utf-8") as stream:
        stream.write("-line\nnext\n")
    resumed = LogTailer(path)
    resumed.start(checkpoint)
    assert resumed.poll() == ["half-line", "next"]
    resumed.close()

    path.unlink()
    path.write_text("fresh\n", encoding="utf-8")
    replaced = LogTailer(path)
    replaced.start(checkpoint)
    assert replaced.poll() == ["fresh"]
    replaced.close()


def test_a_log_created_while_the_notifier_was_down_is_read_from_its_start(tmp_path):
    path = tmp_path / "bridge.log"
    first_run = LogTailer(path)
    first_run.start(None)
    assert first_run.poll() == []
    missing = first_run.checkpoint()
    assert missing == MISSING_FILE_CHECKPOINT
    first_run.close()

    path.write_text("09:26:00 ET  SENT ABC (momo): BUY 100 @ 10.00\n", encoding="utf-8")
    restarted = LogTailer(path)
    restarted.start(missing)
    assert restarted.poll() == ["09:26:00 ET  SENT ABC (momo): BUY 100 @ 10.00"]
    restarted.close()


# --------------------------------------------------------------------------
# e. Daily report
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_daily_report_counts_outcomes_trades_ai_cost_and_screener(tmp_path, monkeypatch):
    await _seed_day(tmp_path, monkeypatch)
    await _add_snapshot(_et(15, 59, 43))
    paths = UserPaths(home=tmp_path / "home")
    _write_bridge_state(
        paths,
        {
            "65172678-bfb": _bracket(
                "BF B", "closed", qty=3741, filled=3741, exit_filled=3741, avg_entry="26.7496",
                avg_exit="26.6627", realized_pnl="-325.09", exit_reason="stop",
            ),
            "abc": _bracket(
                "ABC", "closed", filled=100, exit_filled=100, avg_entry="10.00",
                avg_exit="11.205", realized_pnl="120.50", exit_reason="target",
            ),
            "xyz": _bracket("XYZ", "cancelled"),
            "ghi": _bracket(
                "GHI", "needs_attention", attention_reason="bracket not found at IBKR — check TWS"
            ),
            "def": _bracket("DEF", "skipped", skip_reason="max 3 positions/entries already active"),
            "legacy": _bracket("?", "skipped", skip_reason="legacy entry"),
        },
    )

    close_reads: list[timedelta] = []

    async def close_context(session_date: date, *, now: datetime, max_age: timedelta):
        close_reads.append(max_age)
        return SimpleNamespace(
            summary="Risk-off · S&P -1.2% · Nasdaq -1.6% · 120↑/269↓",
            observed_at=_et(15, 59, 20),
        )

    text = format_daily_report(
        await collect_daily_report(
            SESSION, paths, read_market_context=close_context, now=_et(16, 5)
        )
    )

    # The screener stops at 16:00, so "at close" accepts the day's last context.
    assert close_reads and close_reads[0] >= timedelta(hours=1)
    assert text.splitlines() == [
        "📊 Daily report · Tue 13 Oct 2026",
        "",
        "Decisions by strategy",
        "• MOMO_HOD_V1 (4): 1 would buy, 1 Claude abstained, 1 AI blocked, 1 rule rejected",
        "• ORB_V1 (2): 1 risk blocked, 1 data blocked",
        "",
        "Trades (PAPER bridge)",
        "• BF B (momo): 3741 sh 26.7496 → 26.6627 (stop) · P&L -$325.09",
        "• ABC (momo): 100 sh 10.00 → 11.205 (target) · P&L +$120.50",
        "• XYZ (momo): entry never filled (cancelled)",
        "• ⚠️ GHI (momo): needs attention — bracket not found at IBKR — check TWS",
        "Total realized P&L: -$204.59",
        "Skipped by the bridge: 1 (DEF: max 3 positions/entries already active)",
        "",
        "AI: 3 Claude reviews · $0.14",
        "Market at close: Risk-off · S&P -1.2% · Nasdaq -1.6% · 120↑/269↓ (15:59:20 ET)",
        "Screener: last snapshot 15:59:43 ET",
    ]


@pytest.mark.anyio
async def test_daily_report_falls_back_to_the_legacy_bridge_file_for_this_date(tmp_path, monkeypatch):
    await _use_database(tmp_path, monkeypatch)
    paths = UserPaths(home=tmp_path / "home")
    _write_bridge_state(
        paths,
        {
            "yesterday": _bracket(
                "BF B", "closed", realized_pnl="-325.09", sent_at="2026-10-12T15:50:13+00:00"
            ),
            "today": _bracket(
                "ABC", "open", filled=100, avg_entry="10.00", sent_at="2026-10-13T14:00:00+00:00"
            ),
        },
        legacy=True,
    )

    lines = format_daily_report(
        await collect_daily_report(SESSION, paths, read_market_context=_no_market_context)
    ).splitlines()

    assert "• none recorded" in lines
    assert "• ⚠️ ABC (momo): STILL OPEN 100 sh @ 10.00 · realized $0.00" in lines
    assert not any("BF B" in line for line in lines)
    assert "Screener: last snapshot none today" in lines
    assert "AI: 0 Claude reviews · $0.00" in lines
    assert "Market at close: unavailable" in lines


# --------------------------------------------------------------------------
# The running notifier
# --------------------------------------------------------------------------


def _append(path: Path, *lines: str) -> Callable[[], None]:
    def action() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write("".join(f"{line}\n" for line in lines))

    return action


@pytest.mark.anyio
async def test_run_notifier_forwards_every_source_once_and_resumes_after_restart(
    tmp_path, monkeypatch
):
    await _seed_day(tmp_path, monkeypatch)
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT_ID)
    logs = paths.log_directory
    logs.mkdir(parents=True, exist_ok=True)
    bridge = logs / "bridge.log"
    bridge.write_text("09:40:00 ET  SENT OLD (momo): BUY 1 @ 1.00 | stop 0.90 | target 1.20\n")
    (logs / "screener.log").write_text("")
    _write_bridge_state(paths, {"65172678-bfb": _bracket("BF B", "closed", realized_pnl="-325.09")})

    clock = FakeTime(_et(16, 3, 50))
    clock.at(_et(16, 3, 55), _append(
        bridge,
        "11:50:13 ET  SENT BF B (momo): BUY 3741 @ 26.77 | stop 26.69 | target 26.93 (orders 2-4)",
        "11:50:14 ET  FILL BF B: 1000/3741 sh @ 26.75 (momo)",
        "11:50:15 ET  FILL BF B: 2500/3741 sh @ 26.7499 (momo)",
        "11:50:16 ET  FILL BF B: 3741/3741 sh @ 26.7496 (momo)",
        "11:50:16 ET  order 2: Filled filled=3741 remaining=0 avg=26.7496",
    ))
    clock.at(_et(16, 3, 58), _append(
        bridge,
        "11:51:44 ET  EXIT BF B: 3741 sh @ 26.6627 via stop | P&L -325.09",
        "11:51:44 ET  CLOSED BF B: P&L -325.09 (stop)",
        "11:52:00 ET  SKIP DEF (momo): max 3 positions/entries already active",
        "11:53:00 ET  SKIP GHI (momo): daily loss limit reached",
    ))
    clock.at(_et(16, 4, 0), _append(
        logs / "supervisor.log",
        "2026-10-13T16:04:00-0400 WARNING CHILD_EXIT momo code=1 restart_in=95",
        "2026-10-13T16:04:00-0400 INFO CHILD_EXIT screener code=0 restart_in=none",
    ))
    competing = "ERROR IBKR login active elsewhere (TWS error 10197: competing live session)"
    clock.at(_et(16, 4, 5), _append(logs / "screener.log", competing, competing))
    clock.at(_et(16, 4, 30), _append(logs / "momo.log", "TWS connection lost during bars"))

    async def late_signal() -> None:
        await _add_decision(
            "77777777-late", run_id="shadow-momo-2026-10-13-wl", strategy_id="strategy-momo",
            symbol="LATE", outcome="ai_abstained", created_at=_et(16, 4, 40),
            review=_review("ABSTAIN", "Too extended.", confidence="0.8"),
        )

    clock.at(_et(16, 4, 40), late_signal)
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    sent = telegram.sent()
    assert sent[0] == startup_text(SESSION)
    assert all(payload["chat_id"] == CHAT_ID for method, payload in telegram.calls if method == "sendMessage")
    assert all("parse_mode" not in payload for _, payload in telegram.calls)
    headlines = [text.splitlines()[0] for text in sent]
    for headline in (
        "🛑 ORB_V1 · MMED · risk blocked",
        "🔔 MOMO_HOD_V1 · BF B · WOULD BUY",
        "🚫 MOMO_HOD_V1 · XYZ · AI blocked",
        "🤔 MOMO_HOD_V1 · FIGS · Claude abstained",
        "🤔 MOMO_HOD_V1 · LATE · Claude abstained",
        "⚙️ momo exited (code 1), restarting in 95 s",
        LOGIN_ALERT_TEXT,
    ):
        assert headlines.count(headline) == 1, headline
    fills = [text for text in sent if text.startswith("💵 FILL")]
    assert fills == ["💵 FILL BF B: 3741/3741 sh @ 26.7496 (momo) · 11:50:16 ET (3 fill updates)"]
    bridge_order = [text.split(" ", 2)[1] for text in sent if text.split(" ", 2)[1] in {"SENT", "FILL", "EXIT", "CLOSED"}]
    assert bridge_order == ["SENT", "FILL", "EXIT", "CLOSED"]
    assert any(text.startswith("⛔ SKIP GHI") for text in sent)
    assert not any("DEF" in text or "OLD" in text or "REPLAY" in text for text in sent)
    # One IBKR alert per 10 minutes, whichever log reported first.
    assert sum(1 for text in sent if text.startswith("⚠️ IBKR") or text.startswith("⚠️ TWS")) == 1
    reports = [text for text in sent if text.startswith("📊 Daily report")]
    assert len(reports) == 1 and "• BF B (momo)" in reports[0]
    saved = state_file(paths, SESSION)
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700

    # Restart before 16:15: only what is new since the last run goes out.
    await _add_decision(
        "88888888-new", run_id="shadow-orb-2026-10-13", strategy_id="strategy-orb",
        symbol="NEW", outcome="shadow_accepted", created_at=_et(16, 9),
        review=_review("PROPOSE", "Fresh break."), risk=_approved_risk(),
    )
    _append(bridge, "16:09:00 ET  FLAT: no open engine positions")()
    _append(logs / "screener.log", competing)()
    restarted = FakeTelegram()
    clock = FakeTime(_et(16, 10))

    assert await run_notifier(session_date=SESSION, env=_environment(paths, restarted, clock)) == 0

    assert [text.splitlines()[0] for text in restarted.sent()] == [
        "✅ FLAT: no open engine positions · 16:09:00 ET",
        "🔔 ORB_V1 · NEW · WOULD BUY",
    ]


@pytest.mark.anyio
async def test_undelivered_decisions_are_offered_again_after_a_cooldown(tmp_path, monkeypatch):
    await _seed_day(tmp_path, monkeypatch)
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT_ID)
    outage = [httpx.Response(502, json={"ok": False, "error_code": 502, "description": "down"})] * 3
    telegram = FakeTelegram(replies=outage)  # the startup message is dropped by the sender
    clock = FakeTime(_et(16, 6))

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    sent = telegram.sent()
    assert sent[:3] == [startup_text(SESSION)] * 3
    assert sent.count(startup_text(SESSION)) == 4  # offered again after the cooldown
    assert len([text for text in sent if text.startswith("🔔 MOMO_HOD_V1 · BF B")]) == 1


@pytest.mark.anyio
async def test_run_notifier_is_a_no_op_after_the_session_and_needs_setup_before_it(tmp_path):
    paths = UserPaths(home=tmp_path / "home")
    telegram = FakeTelegram()

    def never_read() -> str:
        raise AssertionError("the Keychain must not be read after 16:15 ET")

    out = io.StringIO()
    late = _environment(paths, telegram, FakeTime(_et(16, 15)), out=out, read_token=never_read)
    assert await run_notifier(session_date=SESSION, env=late) == 0
    assert "nothing to do" in out.getvalue()

    def missing() -> str:
        raise KeychainReadError("unable to read the requested Keychain secret")

    out = io.StringIO()
    early = _environment(paths, telegram, FakeTime(_et(8, 0)), out=out, read_token=missing)
    assert await run_notifier(session_date=SESSION, env=early) == 1
    assert "@BotFather" in out.getvalue()

    no_start = _environment(paths, telegram, FakeTime(_et(8, 0)))
    assert await run_notifier(session_date=SESSION, env=no_start) == 1
    assert telegram.sent() == []
    assert not telegram_config_file(paths).exists()


# --------------------------------------------------------------------------
# autotraderctl notify test
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notify_test_without_a_token_prints_exact_setup_steps_and_sends_nothing(tmp_path):
    def missing() -> str:
        raise KeychainReadError("unable to read the requested Keychain secret")

    telegram = FakeTelegram()
    out = io.StringIO()
    env = _environment(
        UserPaths(home=tmp_path / "home"), telegram, FakeTime(_et(20, 0)), out=out, read_token=missing
    )

    assert await send_test_message(env=env) == 1

    text = out.getvalue()
    assert "@BotFather" in text and "/newbot" in text
    assert (
        "security add-generic-password -a telegram-bot-token"
        " -s com.maccabi.autotrader-v1.telegram -w"
    ) in text
    assert "/start" in text and "autotraderctl notify test" in text
    assert telegram.calls == []


@pytest.mark.anyio
async def test_notify_test_rejects_a_malformed_token_and_explains_a_revoked_one(tmp_path):
    paths = UserPaths(home=tmp_path / "home")
    out = io.StringIO()
    malformed = _environment(paths, FakeTelegram(), FakeTime(_et(20, 0)), out=out, read_token=lambda: "nonsense")
    assert await send_test_message(env=malformed) == 1
    assert "does not look like a Telegram bot token" in out.getvalue()

    unauthorized = FakeTelegram(
        replies=[httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})]
    )
    out = io.StringIO()
    revoked = _environment(paths, unauthorized, FakeTime(_et(20, 0)), out=out)
    assert await send_test_message(env=revoked) == 1
    assert "wrong or was revoked" in out.getvalue()
    assert "security add-generic-password -U" in out.getvalue()
    assert TOKEN not in out.getvalue()


@pytest.mark.anyio
async def test_notify_test_asks_for_start_when_no_private_chat_has_written(tmp_path):
    paths = UserPaths(home=tmp_path / "home")
    telegram = FakeTelegram()
    telegram.updates = [
        {"update_id": 1, "message": {"chat": {"id": -100, "type": "group"}, "text": "/start"}}
    ]
    out = io.StringIO()

    assert await send_test_message(env=_environment(paths, telegram, FakeTime(_et(20, 0)), out=out)) == 1

    assert "/start" in out.getvalue()
    assert telegram.sent() == []
    assert not telegram_config_file(paths).exists()


@pytest.mark.anyio
async def test_notify_test_links_the_chat_caches_it_and_sends_the_greeting(tmp_path):
    paths = UserPaths(home=tmp_path / "home")
    telegram = FakeTelegram()
    telegram.updates = [
        {"update_id": 7, "message": {"chat": {"id": 777, "type": "private", "username": "owner"}, "text": "/start"}}
    ]
    out = io.StringIO()
    env = _environment(paths, telegram, FakeTime(_et(20, 0)), out=out)

    assert await send_test_message(env=env) == 0

    assert [(m, p.get("chat_id"), p.get("text")) for m, p in telegram.calls if m == "sendMessage"] == [
        ("sendMessage", 777, "Autotrader connected ✅")
    ]
    config = telegram_config_file(paths)
    assert json.loads(config.read_text(encoding="utf-8")) == {"chat_id": 777}
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert "@owner" in out.getvalue() and "@at_bot" in out.getvalue()

    telegram.calls.clear()
    assert await send_test_message(env=env) == 0
    assert [method for method, _ in telegram.calls] == ["getMe", "sendMessage"]


@pytest.mark.anyio
async def test_the_token_is_redacted_from_the_private_log_file(
    tmp_path, restore_shadow_logging, capsys
):
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT_ID)
    telegram = FakeTelegram(
        replies=[
            httpx.Response(200, json={"ok": True, "result": {"username": "at_bot"}}),
            httpx.Response(400, json={"ok": False, "error_code": 400, "description": f"echo {TOKEN}"}),
        ]
    )
    out = io.StringIO()
    env = _environment(paths, telegram, FakeTime(_et(20, 0)), out=out, configure_logging=True)

    assert await send_test_message(env=env) == 1
    # Even a direct leak attempt through the shadow logger is scrubbed.
    logging.getLogger("autotrader.shadow.notify").error("leak attempt %s", TOKEN)

    log_text = paths.shadow_log_file.read_text(encoding="utf-8")
    assert "Telegram rejected a message" in log_text
    assert "[REDACTED]" in log_text
    assert TOKEN not in log_text
    assert TOKEN not in out.getvalue()
    assert TOKEN not in capsys.readouterr().err
    assert stat.S_IMODE(paths.shadow_log_file.stat().st_mode) == 0o600
