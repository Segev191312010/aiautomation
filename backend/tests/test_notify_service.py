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
import re
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
    await _seed_run("shadow-momo-2026-10-13-wl", "strategy-momo", "MOMO_HOD_V2")
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
        "shadow-momo-2026-10-12-scan", "strategy-momo", "MOMO_HOD_V2", session_date=date(2026, 10, 12)
    )
    await _add_decision(
        "old-bfb", run_id="shadow-momo-2026-10-12-scan", strategy_id="strategy-momo",
        symbol="OLD", outcome="shadow_accepted", created_at=_et(11, 50) - timedelta(days=1),
        review=_review("PROPOSE", "yesterday"), risk=_approved_risk(),
    )
    await _seed_run("backtest-momo-2026-10-13", "strategy-momo", "MOMO_HOD_V2", mode="backtest")
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
        ("BF B", "MOMO_HOD_V2", "shadow_accepted"),
        ("XYZ", "MOMO_HOD_V2", "ai_blocked"),
        ("FIGS", "MOMO_HOD_V2", "ai_abstained"),
    ]


@pytest.mark.anyio
async def test_decision_messages_read_like_a_trade_ticket(tmp_path, monkeypatch):
    await _seed_day(tmp_path, monkeypatch)
    messages = {d.symbol: format_decision(d) for d in await load_signal_decisions(SESSION)}

    assert messages["BF B"].splitlines() == [
        "🔔 MOMO_HOD_V2 · BF B · WOULD BUY",
        "Buy 3741 @ 26.77 · stop 26.69 · target 26.93",
        "Risk $299.28 (0.08/sh) · reward 2.0R · notional $100,146.57",
        "Claude: PROPOSE · confidence 62%",
        BF_B_RATIONALE,
        "11:50:12 ET · decision 65172678",
    ]
    figs = messages["FIGS"].splitlines()
    assert figs[0] == "🤔 MOMO_HOD_V2 · FIGS · Claude abstained"
    assert figs[1] == "Claude: ABSTAIN · confidence 70%"
    assert len(figs[2]) <= 600 and figs[2].endswith("…")
    assert figs[2].startswith("Entry 17.83 sits 0.636 above vwap")
    mmed = messages["MMED"].splitlines()
    assert mmed[0] == "🛑 ORB_V1 · MMED · risk blocked"
    assert mmed[1] == "Risk codes: max_open_positions, symbol_already_active"
    assert mmed[2] == "Claude: PROPOSE · confidence 66%"
    assert messages["XYZ"].splitlines()[:2] == [
        "🚫 MOMO_HOD_V2 · XYZ · AI blocked",
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
    # Bridge v7a: the owner's per-identity brake and the fail-closed caps/fence skips reach the phone.
    assert text("12:00:00 ET  SKIP GHI (momo): identity_stopped:MOMO_HOD_V2").startswith("⛔ SKIP GHI")
    assert text("12:00:00 ET  SKIP GHI: identity fence unreadable (OperationalError) — fail closed").startswith("⛔ SKIP")
    assert text("12:00:00 ET  SKIP GHI (momo): identity_caps_missing:MOMO_HOD_V2").startswith("⛔ SKIP")
    assert text("12:00:00 ET  SKIP GHI (momo): symbol already has an active bracket") is None
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
        "• MOMO_HOD_V2 (4): 1 would buy, 1 Claude abstained, 1 AI blocked, 1 rule rejected",
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
    # Shadow strategies' decisions come in the half-hourly digest, not one by one.
    digests = [text for text in sent if text.startswith("🗂 Shadow digest")]
    assert len(digests) == 1 and "ORB_V1:\n• MMED risk blocked" in digests[0]
    assert not any(headline.startswith("🛑 ORB_V1") for headline in headlines)
    for headline in (
        "🔔 MOMO_HOD_V2 · BF B · WOULD BUY",
        "🚫 MOMO_HOD_V2 · XYZ · AI blocked",
        "🤔 MOMO_HOD_V2 · FIGS · Claude abstained",
        "🤔 MOMO_HOD_V2 · LATE · Claude abstained",
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

    resent = restarted.sent()
    assert [text.splitlines()[0] for text in resent] == [
        "✅ FLAT: no open engine positions · 16:09:00 ET",
        "🗂 Shadow digest 16:15 ET · 1 decisions (no orders)",
    ]
    assert "ORB_V1:\n• NEW WOULD BUY" in resent[1]


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
    assert len([text for text in sent if text.startswith("🔔 MOMO_HOD_V2 · BF B")]) == 1


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


# --------------------------------------------------------------------------
# IB Gateway unreachable (live 2026-10-14: off all session, no alert)
# --------------------------------------------------------------------------

_SCREENER_FAILED = (
    "2026-10-14 15:00:10,014 WARNING TWS connection failed: TWS PAPER handshake failed"
    " (TWS error 502: Couldn't connect to TWS. Confirm that \"Enable ActiveX and Socket"
    " EClients\" is enabled and connection port is the same as \"Socket Port\" on the TWS)"
)
_BRIDGE_502 = (
    "11:06:47 ET  INFO TWS -1 502 Couldn't connect to TWS. Confirm that \"Enable ActiveX and"
    " Socket EClients\" is enabled and connection port is the same as \"Socket Port\""
)
_BRIDGE_REFUSED = "11:06:57 ET  CRITICAL TWS did not answer on 127.0.0.1:7497 (client 18) — exit 75"
# Live bridge.log 2026-10-14 14:32:16 / 15:24:53 ET (the bridge's own wording).
_BRIDGE_1100 = "14:32:16 ET  CRITICAL TWS -1 1100 Connectivity between IBKR and Trader Workstation has been lost."
_BRIDGE_1102 = (
    "15:24:53 ET  INFO TWS -1 1102 Connectivity between IBKR and Trader Workstation has been restored"
    " - data maintained."
)
_BRIDGE_2110 = (
    "09:25:04 ET  CRITICAL TWS -1 2110 Connectivity between Trader Workstation and server is broken."
    " It will be restored automatically."
)
_CHILD_TRACEBACK = "trading.market.tws_actor.TWSConnectionError: TWS PAPER handshake failed"
_CHILD_FAILED = "error: shadow run --strategy orb-v2 failed: TWSConnectionError: TWS PAPER handshake failed"
_SWEEP_LINE = "2026-10-13 15:10:02,263 INFO sweep 2026-10-13: qualified=396 quoted=396 errors=0 hot=35 seconds=89.8"


def test_broker_evidence_classifies_the_live_lines():
    from trading.operations.notify_service import SCREENER_OPENS, SESSION_OPENS, broker_evidence
    from trading.operations.screener_service import WINDOW_OPENS
    from trading.operations.supervisor import SCREENER_WINDOW

    assert broker_evidence("screener", _SCREENER_FAILED) == "screener_failed"
    assert broker_evidence("screener", _SWEEP_LINE) == "sweep"
    assert broker_evidence("screener", "2026-10-13 15:00:00,014 INFO screener connected to TWS") is None
    # One failed bridge connect attempt writes the 502 line and then its closing
    # CRITICAL: only the latter is a signal (review P1-A F-7: it counted twice).
    assert broker_evidence("bridge", _BRIDGE_502) is None
    assert broker_evidence("bridge", _BRIDGE_REFUSED) == "bridge_refused"
    # IBKR's side lost with the port open, and restored (review P1-A F-6).
    assert broker_evidence("bridge", _BRIDGE_1100) == "ibkr_lost"
    assert broker_evidence("bridge", _BRIDGE_2110) == "ibkr_lost"
    assert broker_evidence("bridge", _BRIDGE_1102) == "ibkr_restored"
    assert broker_evidence("bridge", _BRIDGE_1102.replace(" 1102 ", " 1101 ")) == "ibkr_restored"
    assert broker_evidence("bridge", "10:00:00 ET  INFO TWS -1 2104 Market data farm connection is OK:usfarm") is None
    # The codes as TWS codes only, never as a number elsewhere in a line.
    assert broker_evidence("bridge", "10:00:00 ET  SENT ABC (momo): BUY 1100 @ 11.02") is None
    assert broker_evidence("screener", _BRIDGE_1100) is None
    assert broker_evidence("bridge", "11:50:14 ET  FILL BF B: 1000/3741 sh @ 26.75 (momo)") is None
    assert broker_evidence("orb-v2", _CHILD_FAILED) == "handshake_failed"
    # The traceback's last line precedes the child's own error line: not a second exit.
    assert broker_evidence("orb-v2", _CHILD_TRACEBACK) is None
    assert broker_evidence("momo", "TWS connection lost during bars") is None
    assert broker_evidence("supervisor", "2026-10-14T15:03:03+00:00 CHILD_EXIT orb-v2 code=1 restart_in=300") is None
    # The session opens with the supervisor's first child window; the screener
    # itself connects at its own window open, which the first-sweep grace counts from.
    assert SESSION_OPENS == SCREENER_WINDOW[0]
    assert SCREENER_OPENS == WINDOW_OPENS


def test_broker_watch_thresholds_grace_and_recovery():
    from trading.operations.hysteresis import State
    from trading.operations.notify_service import FIRST_SWEEP_GRACE, BrokerWatch

    watch = BrokerWatch(SESSION)
    # Another date's SESSION_START is not this session's.
    watch.observe("supervisor", "2026-10-12T11:55:00+00:00 SESSION_START date=2026-10-12", _et(7, 55))
    assert watch.opened_at is None
    watch.observe("supervisor", "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13", _et(7, 55))
    assert watch.opened_at == _et(7, 55)

    # Two children's handshake failures are not yet the Gateway; the third is.
    for name, at in (("orb-v2", _et(8, 1)), ("gap-v2", _et(8, 1, 5))):
        assert watch.observe(name, _CHILD_FAILED.replace("orb-v2", name), at) is None
    assert not watch.unreachable
    assert watch.observe("vwap-v2", _CHILD_FAILED.replace("orb-v2", "vwap-v2"), _et(8, 1, 9)) is None
    # Since the streak's first failure (the hysteresis opens at the third).
    assert watch.unreachable_since == _et(8, 1)
    assert watch.alert_text() == "⛔ IB Gateway unreachable since 08:01 ET — log in; the bot has no market data"
    # More evidence keeps the first time; a sweep followed by two clean minutes
    # ends it with one line and resets the streak.
    watch.observe("bridge", _BRIDGE_REFUSED, _et(8, 6))
    assert watch.unreachable_since == _et(8, 1)
    assert watch.observe("screener", _SWEEP_LINE, _et(8, 33)) is None
    assert watch.observe("screener", _SWEEP_LINE, _et(8, 34)) is None
    assert watch.check(_et(8, 35) - timedelta(seconds=1)) is None and watch.unreachable
    assert watch.check(_et(8, 35)) == (
        "✅ IB Gateway reconnected 08:33 ET — market data is back (unreachable since 08:01 ET)"
    )
    assert not watch.unreachable and watch.hysteresis.state is State.CLEAR
    assert watch.observe("screener", _SWEEP_LINE, _et(8, 36)) is None
    assert watch.check(_et(8, 40)) is None

    # No sweep 30 minutes after the screener's 08:00 window opened (not 20 from
    # 07:55, review 904af38 F-2): unreachable since the window opened.
    assert FIRST_SWEEP_GRACE == timedelta(minutes=30)
    silent = BrokerWatch(SESSION)
    silent.session_started(_et(7, 55))
    silent.check(_et(8, 0) + FIRST_SWEEP_GRACE - timedelta(seconds=1))
    assert not silent.unreachable
    silent.check(_et(8, 0) + FIRST_SWEEP_GRACE)
    assert silent.unreachable_since == _et(8, 0)
    assert "since 08:00 ET" in silent.alert_text()
    # A healthy day's first sweep at 08:10 ET keeps it quiet for good.
    healthy = BrokerWatch(SESSION)
    healthy.session_started(_et(7, 55))
    healthy.observe("screener", _SWEEP_LINE, _et(8, 10))
    healthy.check(_et(9, 0))
    assert not healthy.unreachable


def test_broker_watch_folds_the_outages_child_exits_into_its_alert():
    from trading.operations import supervisor
    from trading.operations.notify_service import (
        BRIDGE_CHILD_NAME,
        BRIDGE_RESTART_CODE,
        BrokerWatch,
    )

    assert BRIDGE_CHILD_NAME == supervisor.BRIDGE_CHILD
    assert f"exit {BRIDGE_RESTART_CODE}" in _BRIDGE_REFUSED

    watch = BrokerWatch(SESSION)
    # Before any outage every exit is its own news.
    assert not watch.broker_exit("bridge", 75) and not watch.broker_exit("orb-v2", 1)

    watch.observe("screener", _SCREENER_FAILED, _et(8, 0, 10))
    watch.observe("bridge", _BRIDGE_REFUSED, _et(8, 6, 57))
    watch.observe("orb-v2", _CHILD_FAILED, _et(8, 8))
    assert watch.broker_exit("bridge", 75)
    assert watch.broker_exit("orb-v2", 1)
    assert watch.broker_exit("orb-v2", 1)
    # A child with no handshake line, a bridge exit with another code, a
    # clean exit, and a launch refusal (no code) all go through.
    assert not watch.broker_exit("momo", 1)
    assert not watch.broker_exit("bridge", 1)
    assert not watch.broker_exit("orb-v2", 0)
    assert not watch.broker_exit("bridge", None)
    assert watch.restarting == {"bridge": 1, "orb-v2": 2}
    assert watch.alert_text().endswith("the bot has no market data (2 children restarting)")
    watch.alert_sent()
    assert watch.restarting == {} and "restarting" not in watch.alert_text()
    assert watch.broker_exit("orb-v2", 1)
    assert watch.alert_text().endswith("(1 child restarting)")
    # The sweep forgets who the outage took down; two clean minutes end it.
    assert watch.observe("screener", _SWEEP_LINE, _et(8, 33)) is None
    assert not watch.bridge_refused and not watch.handshake_children and not watch.restarting
    assert not watch.broker_exit("orb-v2", 1)
    assert watch.check(_et(8, 35)) is not None
    assert not watch.broker_exit("orb-v2", 1)


@pytest.mark.anyio
async def test_run_notifier_alerts_once_per_half_hour_while_the_gateway_is_unreachable(
    tmp_path, monkeypatch, caplog
):
    from datetime import time

    from trading.operations import notify_service

    # The eod checker verifies delivery from the notifier log: every alert is logged as queued.
    for name in ("autotrader.shadow", "autotrader.shadow.notify"):
        monkeypatch.setattr(logging.getLogger(name), "propagate", True)
    caplog.set_level(logging.INFO, logger="autotrader.shadow.notify")

    await _use_database(tmp_path, monkeypatch)
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT_ID)
    logs = paths.log_directory
    logs.mkdir(parents=True, exist_ok=True)
    for name in ("bridge.log", "screener.log", "supervisor.log", "orb-v2.log", "gap-v2.log", "vwap-v2.log"):
        (logs / name).write_text("")
    # The notifier's day is cut at 08:36 ET so the simulated minute ticks stay few.
    monkeypatch.setattr(notify_service, "SESSION_END", time(8, 36))

    clock = FakeTime(_et(7, 55))
    clock.at(_et(7, 55, 1), _append(logs / "supervisor.log", "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13"))
    # The screener's reconnect backoff (5, 10, 20, 40 s after a ~10 s handshake timeout).
    for at in (_et(8, 0, 10), _et(8, 0, 25), _et(8, 0, 45), _et(8, 1, 20)):
        clock.at(at, _append(logs / "screener.log", _SCREENER_FAILED))
    clock.at(_et(8, 6, 57), _append(logs / "bridge.log", _BRIDGE_502, _BRIDGE_REFUSED))
    clock.at(_et(8, 7, 0), _append(logs / "supervisor.log", "2026-10-13T12:07:00+00:00 CHILD_EXIT bridge code=75 restart_in=300"))
    for name, at in (("orb-v2", _et(8, 8)), ("gap-v2", _et(8, 8, 2)), ("vwap-v2", _et(8, 8, 4))):
        clock.at(at, _append(logs / f"{name}.log", _CHILD_TRACEBACK, _CHILD_FAILED.replace("orb-v2", name)))
        clock.at(at + timedelta(seconds=10), _append(
            logs / "supervisor.log", f"2026-10-13T12:08:10+00:00 CHILD_EXIT {name} code=1 restart_in=300"
        ))
    # An exit the outage did not cause (no handshake line in momo.log) is still news.
    clock.at(_et(8, 9, 0), _append(logs / "supervisor.log", "2026-10-13T12:09:00+00:00 CHILD_EXIT momo code=1 restart_in=300"))
    for name in ("orb-v2", "gap-v2", "vwap-v2"):
        clock.at(_et(8, 13, 30), _append(logs / "supervisor.log", f"2026-10-13T12:13:30+00:00 CHILD_EXIT {name} code=1 restart_in=300"))
    clock.at(_et(8, 29, 50), _append(logs / "screener.log", _SCREENER_FAILED))
    clock.at(_et(8, 30, 20), _append(logs / "screener.log", _SCREENER_FAILED))
    clock.at(_et(8, 33, 0), _append(logs / "screener.log", _SWEEP_LINE))
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    sent = telegram.sent()
    unreachable = [text for text in sent if text.startswith("⛔ IB Gateway unreachable")]
    reconnected = [text for text in sent if text.startswith("✅ IB Gateway reconnected")]
    # One alert at the third failure in a row, one repeat after the half hour that
    # counts the children the outage keeps restarting, one recovery line.
    assert unreachable == [
        "⛔ IB Gateway unreachable since 08:00 ET — log in; the bot has no market data",
        "⛔ IB Gateway unreachable since 08:00 ET — log in; the bot has no market data"
        " (4 children restarting)",
    ]
    assert reconnected == ["✅ IB Gateway reconnected 08:33 ET — market data is back (unreachable since 08:00 ET)"]
    assert sent.index(unreachable[0]) < sent.index(reconnected[0])
    # The outage's own exits are folded into that count; an unrelated exit still goes through.
    exits = [text for text in sent if text.startswith("⚙️")]
    assert exits == ["⚙️ momo exited (code 1), restarting in 300 s"]
    queued = [record.getMessage() for record in caplog.records if record.getMessage().startswith("alert queued: ")]
    assert queued == [
        "alert queued: broker_unreachable",
        "alert queued: child_exit:momo",
        "alert queued: broker_unreachable",
        "alert queued: broker_reconnected",
    ]
    # The bridge's own "did not answer" CRITICAL came while the alert was open:
    # folded into it (its exit 75 is one of the 4 restarting), not forwarded
    # once per attempt (review P1-A F-7; it used to reach the phone each time).
    assert not any("TWS did not answer" in text for text in sent)
    assert state_file(paths, SESSION).exists()


class TimedTelegram(FakeTelegram):
    """Also records the simulated time each phone message went out."""

    def __init__(self, clock: FakeTime) -> None:
        super().__init__()
        self.clock = clock
        self.sent_at: list[tuple[datetime, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sendMessage"):
            self.sent_at.append((self.clock.now, json.loads(request.content)["text"]))
        return super().handler(request)


def _queued_alerts(caplog) -> list[str]:
    return [
        record.getMessage().removeprefix("alert queued: ")
        for record in caplog.records
        if record.getMessage().startswith("alert queued: ")
    ]


async def _gateway_logs(tmp_path: Path, monkeypatch, caplog, *, until) -> UserPaths:
    from trading.operations import notify_service

    for name in ("autotrader.shadow", "autotrader.shadow.notify"):
        monkeypatch.setattr(logging.getLogger(name), "propagate", True)
    caplog.set_level(logging.INFO, logger="autotrader.shadow.notify")
    await _use_database(tmp_path, monkeypatch)
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT_ID)
    logs = paths.log_directory
    logs.mkdir(parents=True, exist_ok=True)
    for name in ("bridge.log", "screener.log", "supervisor.log", "orb-v2.log", "gap-v2.log", "momo.log"):
        (logs / name).write_text("")
    monkeypatch.setattr(notify_service, "SESSION_END", until)
    return paths


_FLAP_LINE = re.compile(r"^⚡ IB Gateway flapped (?P<count>\d+)× since \d\d:\d\d ET \(held back from separate alerts\)$")


@pytest.mark.anyio
async def test_a_flapping_gateway_sends_one_pair_and_a_flap_count_in_the_digest(
    tmp_path, monkeypatch, caplog
):
    """Review 904af38 F-1: failure/sweep alternating every 70 s for an hour sent ~50 messages."""
    from datetime import time

    # Cut before 09:20, when the (planless) day manager's own outage alert is due.
    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(9, 15))
    screener = paths.log_directory / "screener.log"
    clock = FakeTime(_et(7, 55))
    clock.at(_et(7, 55, 1), _append(paths.log_directory / "supervisor.log", "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13"))
    # A genuine outage at the window open (the screener's 5/10 s reconnect backoff)...
    for at in (_et(8, 0, 10), _et(8, 0, 25), _et(8, 0, 45)):
        clock.at(at, _append(screener, _SCREENER_FAILED))
    # ...then a sweep and a failure alternating every 70 s from 08:10 for an hour,
    # ending on a sweep.
    sweep_at, flaps = _et(8, 10), 0
    while sweep_at + timedelta(seconds=70) <= _et(9, 10):
        clock.at(sweep_at, _append(screener, _SWEEP_LINE))
        clock.at(sweep_at + timedelta(seconds=70), _append(screener, _SCREENER_FAILED))
        flaps += 1
        sweep_at += timedelta(seconds=140)
    clock.at(sweep_at, _append(screener, _SWEEP_LINE))
    last_sweep = sweep_at
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    sent = telegram.sent()
    unreachable = [text for text in sent if text.startswith("⛔ IB Gateway unreachable")]
    reconnected = [text for text in sent if text.startswith("✅ IB Gateway reconnected")]
    # Exactly one pair: no repeat while sweeps keep coming, no pair per blip.
    assert unreachable == ["⛔ IB Gateway unreachable since 08:00 ET — log in; the bot has no market data"]
    assert reconnected == [
        f"✅ IB Gateway reconnected {last_sweep.astimezone(MARKET_TIMEZONE):%H:%M} ET — market data is back"
        " (unreachable since 08:00 ET)"
    ]
    [(closed_at, _text)] = [(at, text) for at, text in telegram.sent_at if text in reconnected]
    assert last_sweep + timedelta(minutes=2) <= closed_at < last_sweep + timedelta(minutes=2, seconds=5)
    assert _queued_alerts(caplog) == ["broker_unreachable", "broker_reconnected"]
    # The flaps reach the phone only as digest lines, and every one is counted.
    digests = [text for text in sent if text.startswith("🗂 Shadow digest")]
    counted = [
        int(match.group("count"))
        for digest in digests
        for match in map(_FLAP_LINE.match, digest.splitlines()[1:])
        if match is not None
    ]
    assert flaps > 20 and sum(counted) == flaps and len(counted) == len(digests) <= 5
    assert sent[0] == startup_text(SESSION)
    assert all(text.startswith(("⛔ IB Gateway", "✅ IB Gateway", "🗂 Shadow digest")) for text in sent[1:])


@pytest.mark.anyio
async def test_a_slow_qualification_until_0828_raises_no_false_alert(tmp_path, monkeypatch, caplog):
    """Review 904af38 F-2: a first sweep after 08:15 ET alerted "unreachable since 07:55"."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(8, 45))
    logs = paths.log_directory
    clock = FakeTime(_et(7, 55))
    clock.at(_et(7, 55, 1), _append(logs / "supervisor.log", "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13"))
    # One transient connect failure, then an 800-name qualification until 08:28.
    clock.at(_et(8, 0, 10), _append(logs / "screener.log", _SCREENER_FAILED))
    clock.at(_et(8, 0, 15), _append(logs / "screener.log", "2026-10-13 12:00:15,002 INFO screener connected to TWS"))
    clock.at(_et(8, 28), _append(
        logs / "screener.log",
        "2026-10-13 12:28:00,410 INFO universe qualified for 2026-10-13: 803 qualified, 0 transient errors",
    ))
    clock.at(_et(8, 29, 40), _append(logs / "screener.log", _SWEEP_LINE))
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    assert telegram.sent() == [startup_text(SESSION)]
    assert _queued_alerts(caplog) == []


@pytest.mark.anyio
async def test_a_genuine_outage_alerts_within_two_minutes_and_folds_its_exits(tmp_path, monkeypatch, caplog):
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(11, 40))
    logs = paths.log_directory
    supervisor_log = logs / "supervisor.log"
    clock = FakeTime(_et(10, 58))
    clock.at(_et(10, 59), _append(logs / "screener.log", _SWEEP_LINE))
    # The Gateway goes away at 11:00: the screener's reconnect backoff, then every 70 s.
    failures = [_et(11, 0, 10), _et(11, 0, 25), _et(11, 0, 45), _et(11, 1, 15), _et(11, 2, 5)]
    failures += [_et(11, 2, 5) + timedelta(seconds=70 * n) for n in range(1, 28)]
    for at in failures:
        clock.at(at, _append(logs / "screener.log", _SCREENER_FAILED))
    for at in (_et(11, 1), _et(11, 11)):
        clock.at(at, _append(logs / "bridge.log", _BRIDGE_502, _BRIDGE_REFUSED))
        clock.at(at + timedelta(seconds=5), _append(
            supervisor_log, "2026-10-13T15:01:05+00:00 CHILD_EXIT bridge code=75 restart_in=300"
        ))
    for name, at in (("orb-v2", _et(11, 2)), ("gap-v2", _et(11, 2, 3)), ("orb-v2", _et(11, 7)), ("gap-v2", _et(11, 7, 3))):
        clock.at(at, _append(logs / f"{name}.log", _CHILD_TRACEBACK, _CHILD_FAILED.replace("orb-v2", name)))
        clock.at(at + timedelta(seconds=10), _append(
            supervisor_log, f"2026-10-13T15:02:10+00:00 CHILD_EXIT {name} code=1 restart_in=300"
        ))
    # Not the outage's (no handshake line in momo.log): still news.
    clock.at(_et(11, 4), _append(supervisor_log, "2026-10-13T15:04:00+00:00 CHILD_EXIT momo code=1 restart_in=300"))
    clock.at(_et(11, 35), _append(logs / "screener.log", _SWEEP_LINE))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    alerts = [(at, text) for at, text in telegram.sent_at if text.startswith(("⛔ IB", "✅ IB"))]
    assert [text for _at, text in alerts] == [
        "⛔ IB Gateway unreachable since 11:00 ET — log in; the bot has no market data",
        "⛔ IB Gateway unreachable since 11:00 ET — log in; the bot has no market data"
        " (3 children restarting)",
        "✅ IB Gateway reconnected 11:35 ET — market data is back (unreachable since 11:00 ET)",
    ]
    assert alerts[0][0] - failures[0] <= timedelta(minutes=2)
    assert alerts[1][0] - alerts[0][0] >= timedelta(minutes=30)
    assert alerts[2][0] >= _et(11, 37)
    exits = [text for text in telegram.sent() if text.startswith("⚙️")]
    assert exits == ["⚙️ momo exited (code 1), restarting in 300 s"]
    assert _queued_alerts(caplog) == [
        "broker_unreachable", "child_exit:momo", "broker_unreachable", "broker_reconnected",
    ]


def _between(moment: datetime, start: datetime, *, slack: timedelta = timedelta(seconds=5)) -> bool:
    """The notifier sends from its next 1-second cycle: allow a few simulated seconds."""
    return start <= moment < start + slack


@pytest.mark.anyio
async def test_a_real_outage_right_after_an_announced_blip_pages_within_the_sustain_limit(
    tmp_path, monkeypatch, caplog
):
    """Review P1-A F-1 (the reviewer's blip-then-outage probe): a blip paged at
    08:16, so the one-pair-per-30-min budget held the real 08:25 outage's page
    until 08:46:10 and folded its child exits into an alert the phone never had."""
    from datetime import time

    from trading.operations.hysteresis import SUSTAIN_LIMIT

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(9, 20))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et(8, 9))
    clock.at(_et(8, 10), _append(screener, _SWEEP_LINE))
    # A 70-second blip: three failed connects 08:15:00-08:16:10, sweeps again from 08:17.
    for at in (_et(8, 15, 0), _et(8, 15, 35), _et(8, 16, 10)):
        clock.at(at, _append(screener, _SCREENER_FAILED))
    for n in range(7):
        clock.at(_et(8, 17) + timedelta(seconds=65 * n), _append(screener, _SWEEP_LINE))
    # The Gateway dies for real at 08:25 (a failed connect every 70 s) until 09:11.
    outage = _et(8, 25)
    for n in range(38):
        clock.at(outage + timedelta(seconds=70 * n), _append(screener, _SCREENER_FAILED))
    clock.at(_et(9, 11), _append(screener, _SWEEP_LINE))
    # A child the outage takes down: once while its page is held back, once after.
    for at in (_et(8, 28), _et(8, 35)):
        exited = at + timedelta(seconds=10)
        clock.at(at, _append(logs / "momo.log", _CHILD_TRACEBACK, _CHILD_FAILED.replace("orb-v2", "momo")))
        clock.at(exited, _append(
            logs / "supervisor.log", f"{exited.isoformat(timespec='seconds')} CHILD_EXIT momo code=1 restart_in=300"
        ))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    broker = [(at, text) for at, text in telegram.sent_at if text.startswith(("⛔ IB", "✅ IB"))]
    assert [text for _at, text in broker] == [
        "⛔ IB Gateway unreachable since 08:15 ET — log in; the bot has no market data",
        "✅ IB Gateway reconnected 08:17 ET — market data is back (unreachable since 08:15 ET)",
        "⛔ IB Gateway unreachable since 08:25 ET — log in; the bot has no market data",
        "⛔ IB Gateway unreachable since 08:25 ET — log in; the bot has no market data (1 child restarting)",
        "✅ IB Gateway reconnected 09:11 ET — market data is back (unreachable since 08:25 ET)",
    ]
    # The blip's pair still goes out at once.
    assert _between(broker[0][0], _et(8, 16, 10)) and _between(broker[1][0], _et(8, 19))
    # The real outage pages at the first failure that is SUSTAIN_LIMIT into an
    # unbroken run (08:30:50: failures every 70 s from 08:25:00), not at 08:46:10.
    assert SUSTAIN_LIMIT == timedelta(minutes=5)
    assert _between(broker[2][0], _et(8, 30, 50))
    assert broker[2][0] - outage <= SUSTAIN_LIMIT + timedelta(seconds=70 + 5)
    assert _between(broker[3][0], _et(9, 0, 50)) and _between(broker[4][0], _et(9, 13))
    # Held back, the outage's exit is forwarded as before; announced, it is folded
    # into the alert (the repeat counts it).
    exits = [(at, text) for at, text in telegram.sent_at if text.startswith("⚙️")]
    assert [text for _at, text in exits] == ["⚙️ momo exited (code 1), restarting in 300 s"]
    assert _between(exits[0][0], _et(8, 28, 10))


def test_a_held_back_outage_forwards_its_exits_until_the_sustain_limit_announces_it():
    from trading.operations.notify_service import BrokerWatch

    watch = BrokerWatch(SESSION)
    watch.session_started(_et(7, 55))
    watch.observe("screener", _SWEEP_LINE, _et(8, 10))
    for at in (_et(8, 15, 0), _et(8, 15, 35), _et(8, 16, 10)):
        watch.observe("screener", _SCREENER_FAILED, at)
    assert watch.unreachable
    watch.observe("screener", _SWEEP_LINE, _et(8, 17))
    assert watch.check(_et(8, 19)) is not None and not watch.unreachable
    # Three failures from 08:25: open, but the pair budget holds it back.
    for at in (_et(8, 25, 0), _et(8, 26, 10), _et(8, 27, 20)):
        watch.observe("screener", _SCREENER_FAILED, at)
    watch.observe("momo", _CHILD_FAILED.replace("orb-v2", "momo"), _et(8, 27, 30))
    watch.observe("bridge", _BRIDGE_REFUSED, _et(8, 27, 40))
    assert watch.hysteresis.is_open and not watch.unreachable
    # No alert on the phone: its exits and the bridge's line are news of their own.
    assert not watch.broker_exit("momo", 1) and not watch.broker_exit("bridge", 75)
    assert not watch.folds_bridge_line(_BRIDGE_REFUSED)
    assert watch.restarting == {}
    # Time alone does not announce it: the last failure is 2 min 40 s into the run.
    assert watch.check(_et(8, 31)) is None and not watch.unreachable
    # A failure five minutes or more into the unbroken run: announced, whatever the budget.
    watch.observe("screener", _SCREENER_FAILED, _et(8, 31, 10))
    assert watch.unreachable_since == _et(8, 25)
    assert watch.broker_exit("momo", 1) and watch.folds_bridge_line(_BRIDGE_REFUSED)


def test_one_bridge_connect_attempt_is_one_failure_signal():
    """Review P1-A F-7: the 502 line and the "did not answer" CRITICAL of one
    attempt counted as two of the three signals."""
    from trading.operations.notify_service import BrokerWatch

    watch = BrokerWatch(SESSION)
    watch.session_started(_et(9, 0))
    watch.observe("screener", _SWEEP_LINE, _et(10, 0))
    watch.observe("bridge", _BRIDGE_502, _et(10, 0, 30))
    watch.observe("bridge", _BRIDGE_REFUSED, _et(10, 0, 40))
    watch.observe("momo", _CHILD_FAILED.replace("orb-v2", "momo"), _et(10, 1, 0))
    assert not watch.unreachable
    # Before the alert the bridge's CRITICAL is forwarded; a third signal opens it.
    assert not watch.folds_bridge_line(_BRIDGE_REFUSED)
    watch.observe("orb-v2", _CHILD_FAILED, _et(10, 1, 20))
    assert watch.unreachable_since == _et(10, 0, 40)
    assert watch.folds_bridge_line(_BRIDGE_REFUSED)
    # Only that line: the bridge's other lines still reach the phone.
    for line in (_BRIDGE_502, _BRIDGE_1100, "10:02:00 ET  FILL ABC: 10/10 sh @ 1.00 (momo)"):
        assert not watch.folds_bridge_line(line)


def test_an_ibkr_loss_not_restored_within_two_minutes_opens_the_alert_and_a_quick_restore_does_not():
    from trading.operations.notify_service import BrokerWatch

    blip = BrokerWatch(SESSION)
    blip.session_started(_et(9, 0))
    blip.observe("screener", _SWEEP_LINE, _et(14, 31))
    blip.observe("bridge", _BRIDGE_1100, _et(14, 32, 16))
    blip.observe("bridge", _BRIDGE_1102, _et(14, 32, 46))
    for second in range(0, 600, 10):
        blip.check(_et(14, 32, 50) + timedelta(seconds=second))
    assert not blip.unreachable and blip.ibkr_lost_at is None

    lost = BrokerWatch(SESSION)
    lost.session_started(_et(9, 0))
    lost.observe("screener", _SWEEP_LINE, _et(14, 31))
    lost.observe("bridge", _BRIDGE_1100, _et(14, 32, 16))
    assert lost.check(_et(14, 34, 15)) is None and not lost.unreachable
    lost.check(_et(14, 34, 16))
    assert lost.unreachable_since == _et(14, 32, 16)
    # The restore is success evidence: two clean minutes later, the recovery line.
    assert lost.observe("bridge", _BRIDGE_1102, _et(15, 24, 53)) is None
    assert lost.check(_et(15, 26, 53)) == (
        "✅ IB Gateway reconnected 15:24 ET — market data is back (unreachable since 14:32 ET)"
    )


@pytest.mark.anyio
async def test_the_real_1014_afternoon_ibkr_loss_with_the_port_open_reaches_the_phone(
    tmp_path, monkeypatch, caplog
):
    """Review P1-A F-6: live 2026-10-14 14:32-15:25 ET IBKR's side was lost with
    the port open (TWS 1100, the screener's snapshots timing out, no "TWS
    connection failed"); BrokerWatch saw nothing and only two CRITICAL lines went out."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(15, 40))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et(14, 25))
    for n in range(7):
        clock.at(_et(14, 25, 30) + timedelta(seconds=65 * n), _append(screener, _SWEEP_LINE))
    clock.at(_et(14, 32, 16), _append(logs / "bridge.log", _BRIDGE_1100))
    sweep_failed = (
        "2026-10-14 21:33:48,864 ERROR screener sweep failed: 25 consecutive snapshot failures; last:"
        " TWS market snapshot timed out without real-time confirmation, a last trade or bid/ask, prior close"
    )
    for n in range(38):
        clock.at(_et(14, 33, 48) + timedelta(seconds=83 * n), _append(
            screener, sweep_failed, "2026-10-14 21:33:49,872 INFO screener connected to TWS"
        ))
    clock.at(_et(15, 24, 50), _append(logs / "bridge.log", _BRIDGE_1100.replace("14:32:16", "15:24:50")))
    clock.at(_et(15, 24, 53), _append(logs / "bridge.log", _BRIDGE_1102))
    for n in range(10):
        clock.at(_et(15, 26, 5) + timedelta(seconds=62 * n), _append(screener, _SWEEP_LINE))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    broker = [(at, text) for at, text in telegram.sent_at if text.startswith(("⛔ IB", "✅ IB"))]
    assert [text for _at, text in broker] == [
        "⛔ IB Gateway unreachable since 14:32 ET — log in; the bot has no market data",
        "⛔ IB Gateway unreachable since 14:32 ET — log in; the bot has no market data",
        "✅ IB Gateway reconnected 15:24 ET — market data is back (unreachable since 14:32 ET)",
    ]
    assert _between(broker[0][0], _et(14, 34, 16))
    assert _between(broker[1][0], _et(15, 4, 16))
    assert _between(broker[2][0], _et(15, 26, 53))
    # The bridge's own 1100 CRITICAL lines still go out, as before.
    assert sum(text.startswith("⚠️ CRITICAL TWS -1 1100") for _at, text in telegram.sent_at) == 2


@pytest.mark.anyio
async def test_a_supervisor_stop_cancels_a_suspension_page(tmp_path, monkeypatch, caplog):
    """Review P1-A F-4: a deploy or restart while momo was suspended left the
    phone's last word "suspended" while momo ran again."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(10, 40))
    logs = paths.log_directory
    supervisor_log = logs / "supervisor.log"
    clock = FakeTime(_et(10, 0))
    for minute in (0, 15, 30):
        clock.at(_et(10, minute, 30), _append(logs / "screener.log", _SWEEP_LINE))
    clock.at(_et(10, 15), _append(supervisor_log, "2026-10-13T14:15:00+00:00 CHILD_SUSPENDED momo count=6 window=30m"))
    clock.at(_et(10, 20), _append(
        supervisor_log,
        "2026-10-13T14:20:00+00:00 CHILD_RESUMED momo reason=supervisor_stop",
        "2026-10-13T14:20:00+00:00 SIGNAL momo SIGTERM reason=shutdown",
        "2026-10-13T14:20:01+00:00 SUPERVISOR_STOP reason=SIGTERM",
        "2026-10-13T14:20:30+00:00 SUPERVISOR_START pid=999 mode=daily",
        "2026-10-13T14:20:30+00:00 SESSION_START date=2026-10-13",
        "2026-10-13T14:20:31+00:00 CHILD_START momo pid=1000",
    ))
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    assert [text for text in telegram.sent() if "momo" in text] == [
        "⛔ momo suspended after 6 restarts in 30 min (relaunch probe every 5 min)",
        "✅ momo resumed (supervisor_stop)",
    ]


def test_restart_cap_lines_read_as_phone_messages():
    from trading.operations.notify_service import restart_cap_event, supervisor_child_name

    suspended = "2026-10-20T14:00:05+00:00 CHILD_SUSPENDED momo count=6 window=30m"
    resumed = "2026-10-20T14:05:00+00:00 CHILD_RESUMED momo reason=port_open"
    storm = "2026-10-20T14:03:00+00:00 CHILD_RESTART_STORM bridge count=6 window=30m"
    assert supervisor_event_text(suspended) == (
        "⛔ momo suspended after 6 restarts in 30 min (relaunch probe every 5 min)"
    )
    assert supervisor_event_text(resumed) == "✅ momo resumed (port_open)"
    for reason in ("probe_healthy", "supervisor_stop"):
        assert supervisor_event_text(resumed.replace("port_open", reason)) == f"✅ momo resumed ({reason})"
    # At its window end the child does not run again today (review P1-A F-5).
    assert supervisor_event_text(resumed.replace("port_open", "window_boundary")) == (
        "✅ momo done for today (window ended)"
    )
    assert supervisor_event_text(storm) == (
        "⚠️ bridge restart storm: 6 in 30 min (positions protected at IB; backoff continues)"
    )
    assert [restart_cap_event(line)[:2] for line in (suspended, resumed, storm)] == [
        ("child_suspended", "momo"), ("child_resumed", "momo"), ("child_restart_storm", "bridge"),
    ]
    assert [supervisor_child_name(line) for line in (suspended, resumed, storm)] == ["momo", "momo", "bridge"]
    # A drifted format is forwarded raw, never dropped; other lines are not these.
    drifted = "2026-10-20T14:00:05+00:00 CHILD_SUSPENDED orb-v2 restarts=6"
    assert restart_cap_event(drifted) == ("child_suspended", "orb-v2", f"⚠️ supervisor: {drifted}")
    assert restart_cap_event("2026-10-20T14:00:05+00:00 CHILD_EXIT momo code=1 restart_in=95") is None
    assert restart_cap_event("2026-10-20T14:00:05+00:00 CHILD_START momo pid=12") is None


@pytest.mark.anyio
async def test_restart_cap_lines_reach_the_phone_once_per_child_per_half_hour(tmp_path, monkeypatch, caplog):
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(10, 45))
    supervisor_log = paths.log_directory / "supervisor.log"

    def line(at: datetime, event: str) -> None:
        clock.at(at, _append(supervisor_log, f"{at.isoformat(timespec='seconds')} {event}"))

    clock = FakeTime(_et(10, 0))
    # The Gateway is fine: its sweeps keep the broker watch quiet.
    for minute in (0, 15, 30):
        clock.at(_et(10, minute, 30), _append(paths.log_directory / "screener.log", _SWEEP_LINE))
    line(_et(10, 0, 1), "CHILD_EXIT momo code=1 restart_in=300")
    line(_et(10, 0, 5), "CHILD_SUSPENDED momo count=6 window=30m")
    line(_et(10, 1), "CHILD_SUSPENDED momo count=6 window=30m")  # same news: held back
    line(_et(10, 2), "CHILD_SUSPENDED orb-v2 count=6 window=30m")  # another child
    line(_et(10, 3), "CHILD_RESTART_STORM bridge count=6 window=30m")
    line(_et(10, 5), "CHILD_RESUMED momo reason=port_open")  # contradicts: goes at once
    line(_et(10, 6), "CHILD_RESUMED momo reason=probe_healthy")  # same news: held back
    line(_et(10, 7), "CHILD_SUSPENDED momo count=7 window=30m")  # contradicts: goes
    line(_et(10, 20), "CHILD_RESTART_STORM bridge count=9 window=30m")  # held back
    line(_et(10, 33, 30), "CHILD_RESTART_STORM bridge count=12 window=30m")  # half an hour later
    line(_et(10, 40), "CHILD_RESUMED momo reason=window_boundary")
    line(_et(10, 41), "CHILD_SUSPENDED momo count=6 window=30m")
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    def phone(fake: FakeTelegram) -> list[str]:
        # The planless day manager's own outage alert (after 09:20) is not under test.
        return [text for text in fake.sent() if not text.startswith("⚠️ AI day manager")]

    assert phone(telegram)[1:] == [
        "⚙️ momo exited (code 1), restarting in 300 s",
        "⛔ momo suspended after 6 restarts in 30 min (relaunch probe every 5 min)",
        "⛔ orb-v2 suspended after 6 restarts in 30 min (relaunch probe every 5 min)",
        "⚠️ bridge restart storm: 6 in 30 min (positions protected at IB; backoff continues)",
        "✅ momo resumed (port_open)",
        "⛔ momo suspended after 7 restarts in 30 min (relaunch probe every 5 min)",
        "⚠️ bridge restart storm: 12 in 30 min (positions protected at IB; backoff continues)",
        "✅ momo done for today (window ended)",
        "⛔ momo suspended after 6 restarts in 30 min (relaunch probe every 5 min)",
    ]
    assert _queued_alerts(caplog) == [
        "child_exit:momo", "child_suspended:momo", "child_suspended:orb-v2", "child_restart_storm:bridge",
        "child_resumed:momo", "child_suspended:momo", "child_restart_storm:bridge", "child_resumed:momo",
        "child_suspended:momo",
    ]

    # A restart keeps what the phone last heard: the same news is still held back,
    # the contradicting one still goes.
    caplog.clear()
    clock = FakeTime(_et(10, 46))
    clock.at(_et(10, 46, 30), _append(paths.log_directory / "screener.log", _SWEEP_LINE))
    line(_et(10, 47), "CHILD_SUSPENDED momo count=6 window=30m")
    line(_et(10, 48), "CHILD_RESUMED momo reason=port_open")
    restarted = FakeTelegram()
    from trading.operations import notify_service

    monkeypatch.setattr(notify_service, "SESSION_END", time(10, 50))
    assert await run_notifier(session_date=SESSION, env=_environment(paths, restarted, clock)) == 0
    assert phone(restarted) == ["✅ momo resumed (port_open)"]
    assert _queued_alerts(caplog) == ["child_resumed:momo"]


def test_real_decisions_are_exactly_the_sendable_identities(monkeypatch):
    from trading import sendable_identities
    from trading.operations.notify_service import SignalDecision, is_real_decision

    def decision(strategy):
        return SignalDecision(decision_id="d1", created_at=None, strategy=strategy, symbol="AAA",
                              outcome="shadow_accepted", review={}, risk=None)

    assert is_real_decision(decision("MOMO_HOD_V2"))
    # Exactly the grant, whichever version of the tuple ships (v7a: MOMO only; v7b: four).
    assert is_real_decision(decision("ORB_V2")) is ("ORB_V2" in sendable_identities.SENDABLE_IDENTITIES)
    assert not is_real_decision(decision("ORB_V9"))
    assert not is_real_decision(decision("MOMO_HOD_V1"))  # a prefix match is no longer enough
    assert not is_real_decision(decision("AP_MOMO_HOD_V2"))
    monkeypatch.setattr(sendable_identities, "SENDABLE_IDENTITIES", ("MOMO_HOD_V2", "ORB_V2"))
    assert is_real_decision(decision("ORB_V2"))  # v7b: the grant reaches the phone at once
    assert not is_real_decision(decision("VWAP_PB_V2"))
