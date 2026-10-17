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
# The alert wordings (review P1-A.1 L-3, P1-A.3): the Gateway does not answer (a
# failed connect, no first sweep), it is logged in and its link to IBKR is down (TWS
# 1100/2110), or it is connected and IBKR refuses the data (daily-history timeouts, a
# failed sweep). The IBKR-side two give the owner's two steps of GW-HMDS-1016, in
# that order (live 2026-10-16: logging out of the app restored the orders, only a
# Gateway restart restored the price history), and say "restart now" once the
# outage has lasted 15 minutes. Spelled out here, not imported, so a wording change
# in the module fails these tests.
_GATEWAY_ALERT = "⛔ IB Gateway unreachable since {since} ET — log in; the bot has no market data"
_IBKR_LOST_ALERT = (
    "⛔ IBKR connectivity lost since {since} ET — the Gateway is still logged in;"
    " 1) log out of the IBKR app and website, 2) if it lasts, restart IB Gateway:"
    " File → Exit, reopen, log in"
)
_DATA_REFUSED_ALERT = (
    "⛔ IBKR is refusing prices since {since} ET (Gateway connected) —"
    " 1) log out of the IBKR app and website, 2) if it lasts, restart IB Gateway:"
    " File → Exit, reopen, log in"
)
_RESTART_NOW_ALERT = (
    "⛔ IBKR still refusing prices since {since} ET — restart IB Gateway now: File → Exit, reopen, log in"
)
# Live screener.log 2026-10-16 (GW-HMDS-1016), times as logged (the Mac's local
# time, ET+7): every universe qualification timed out after the 20-second TWS
# request timeout; the progress line counts attempts, not successes.
_HISTORY_TIMEOUT = "2026-10-16 15:00:20,048 WARNING qualification of A failed: error: TWS daily-history request timed out"
_QUALIFIED_PROGRESS = "2026-10-16 15:16:40,594 INFO qualified 50/503 instruments"
_SWEEP_FAILED = (
    "2026-10-14 21:33:48,864 ERROR screener sweep failed: 25 consecutive snapshot failures; last:"
    " TWS market snapshot timed out without real-time confirmation, a last trade or bid/ask, prior close"
)


def _timeout_line(symbol: str) -> str:
    return _HISTORY_TIMEOUT.replace("qualification of A ", f"qualification of {symbol} ")


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
    # More evidence keeps the first time; three sweeps in a row and two clean
    # minutes from the first (P1-A.4) end it with one line and reset the streak.
    watch.observe("bridge", _BRIDGE_REFUSED, _et(8, 6))
    assert watch.unreachable_since == _et(8, 1)
    assert watch.observe("screener", _SWEEP_LINE, _et(8, 33)) is None
    assert watch.observe("screener", _SWEEP_LINE, _et(8, 34)) is None
    assert watch.observe("screener", _SWEEP_LINE, _et(8, 34, 30)) is None
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
    # The sweep forgets who the outage took down; three sweeps and two clean
    # minutes end it (P1-A.4).
    assert watch.observe("screener", _SWEEP_LINE, _et(8, 33)) is None
    assert not watch.bridge_refused and not watch.handshake_children and not watch.restarting
    assert not watch.broker_exit("orb-v2", 1)
    watch.observe("screener", _SWEEP_LINE, _et(8, 34))
    watch.observe("screener", _SWEEP_LINE, _et(8, 34, 30))
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
    # Back: three sweeps (the recovery line needs three in a row over two minutes, P1-A.4).
    for at in (_et(8, 33, 0), _et(8, 34, 5), _et(8, 35, 10)):
        clock.at(at, _append(logs / "screener.log", _SWEEP_LINE))
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
async def test_a_flapping_gateway_is_one_outage_with_its_half_hour_repeats_and_a_flap_count(
    tmp_path, monkeypatch, caplog
):
    """Review 904af38 F-1: failure/sweep alternating every 70 s for an hour sent ~50
    messages. It is one outage: no pair per blip, the flips are the digest's flap
    count. P1-A.4 (owner 2026-10-17, review N14 MEDIUM-1): a lone sweep between two
    failures never clears it, so its half-hour repeats go on while it flaps, and
    the recovery line needs three sweeps in a row over two minutes."""
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
    # ending on three sweeps 65 s apart: the recovery.
    sweep_at, flaps = _et(8, 10), 0
    while sweep_at + timedelta(seconds=70) <= _et(9, 10):
        clock.at(sweep_at, _append(screener, _SWEEP_LINE))
        clock.at(sweep_at + timedelta(seconds=70), _append(screener, _SCREENER_FAILED))
        flaps += 1
        sweep_at += timedelta(seconds=140)
    last_sweep = sweep_at
    for n in range(3):
        clock.at(last_sweep + timedelta(seconds=65 * n), _append(screener, _SWEEP_LINE))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    sent = telegram.sent()
    unreachable = [(at, text) for at, text in telegram.sent_at if text.startswith("⛔ IB Gateway unreachable")]
    reconnected = [text for text in sent if text.startswith("✅ IB Gateway reconnected")]
    # One alert, then a repeat every half hour while it still fails between the
    # sweeps (the newest evidence at 08:30:45 is the 08:29:50 failure, at 09:00:45
    # the 09:00:10 one), and one recovery line: no pair per blip.
    alert = "⛔ IB Gateway unreachable since 08:00 ET — log in; the bot has no market data"
    assert [text for _at, text in unreachable] == [alert, alert, alert]
    assert _between(unreachable[0][0], _et(8, 0, 45))
    assert _between(unreachable[1][0], _et(8, 30, 45)) and _between(unreachable[2][0], _et(9, 0, 45))
    assert reconnected == [
        f"✅ IB Gateway reconnected {last_sweep.astimezone(MARKET_TIMEZONE):%H:%M} ET — market data is back"
        " (unreachable since 08:00 ET)"
    ]
    # At the third sweep (2 min 10 s after the first), not two minutes after the first.
    [(closed_at, _text)] = [(at, text) for at, text in telegram.sent_at if text in reconnected]
    assert _between(closed_at, last_sweep + timedelta(seconds=130))
    assert _queued_alerts(caplog) == [
        "broker_unreachable", "broker_unreachable", "broker_unreachable", "broker_reconnected",
    ]
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
    # Back: three sweeps (P1-A.4), the recovery line at the third.
    for at in (_et(11, 35), _et(11, 36, 5), _et(11, 37, 10)):
        clock.at(at, _append(logs / "screener.log", _SWEEP_LINE))
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
    assert _between(alerts[2][0], _et(11, 37, 10))
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
    for n in range(3):  # back: three sweeps (P1-A.4)
        clock.at(_et(9, 11) + timedelta(seconds=65 * n), _append(screener, _SWEEP_LINE))
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
    # The blip's pair still goes out at once (its recovery line at its third sweep,
    # 08:19:10: three in a row over two minutes, P1-A.4).
    assert _between(broker[0][0], _et(8, 16, 10)) and _between(broker[1][0], _et(8, 19, 10))
    # The real outage pages at the first failure that is SUSTAIN_LIMIT into an
    # unbroken run (08:30:50: failures every 70 s from 08:25:00), not at 08:46:10.
    assert SUSTAIN_LIMIT == timedelta(minutes=5)
    assert _between(broker[2][0], _et(8, 30, 50))
    assert broker[2][0] - outage <= SUSTAIN_LIMIT + timedelta(seconds=70 + 5)
    assert _between(broker[3][0], _et(9, 0, 50)) and _between(broker[4][0], _et(9, 13, 10))
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
    for at in (_et(8, 17), _et(8, 18), _et(8, 18, 30)):  # three sweeps (P1-A.4)
        watch.observe("screener", _SWEEP_LINE, at)
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
    # The Gateway is still logged in: its own wording, never "log in" (review P1-A.1 L-3).
    assert lost.alert_text() == _IBKR_LOST_ALERT.format(since="14:32")
    # The restore is success evidence, the first of the three in a row the recovery
    # line needs (P1-A.4): with the screener's next two sweeps (live 2026-10-14:
    # 15:26:05, 15:27:07), at the third, 2 min 14 s after the restore.
    assert lost.observe("bridge", _BRIDGE_1102, _et(15, 24, 53)) is None
    assert lost.observe("screener", _SWEEP_LINE, _et(15, 26, 5)) is None
    assert lost.check(_et(15, 26, 53)) is None and lost.unreachable
    assert lost.observe("screener", _SWEEP_LINE, _et(15, 27, 7)) == (
        "✅ IB Gateway reconnected 15:24 ET — market data is back (unreachable since 14:32 ET)"
    )


def _bridge_window_end(session_date: date) -> datetime:
    from trading.operations.supervisor import plan_session
    from trading.scheduler.sessions import market_session

    [bridge] = [plan for plan in plan_session(market_session(session_date)) if plan.name == "bridge"]
    return bridge.ends_at


def test_an_ibkr_loss_is_unknown_not_lost_once_the_bridges_window_ends():
    """Review P1-A.1 L-2: the bridge logs 1100 at 15:59:00; IBKR is back at
    16:00:30, but no client is left to log 1102 and the screener sweeps no more.
    The 16:01 re-assertion paged "unreachable since 15:59 ET — log in"."""
    from trading.operations.notify_service import BrokerWatch

    # Only the window end here (no supervisor line): nothing is re-asserted after it.
    late = BrokerWatch(SESSION)
    late.session_started(_et(7, 55))
    late.observe("screener", _SWEEP_LINE, _et(15, 58, 30))
    late.observe("bridge", _BRIDGE_1100.replace("14:32:16", "15:59:00"), _et(15, 59))
    moment = _et(15, 59, 1)
    while moment <= _et(16, 15):
        assert late.check(moment) is None, moment
        assert not late.unreachable, moment
        moment += timedelta(seconds=1)
    assert late.ibkr_lost_at is None and not late.hysteresis.is_open and not late.flaps_pending

    # In session the same loss, not restored within two minutes, still opens the
    # alert, in its own wording: 15:57:00 lost, re-asserted (and opened) at 15:59:00.
    in_session = BrokerWatch(SESSION)
    in_session.session_started(_et(7, 55))
    in_session.observe("screener", _SWEEP_LINE, _et(15, 56, 30))
    in_session.observe("bridge", _BRIDGE_1100.replace("14:32:16", "15:57:00"), _et(15, 57))
    in_session.check(_et(15, 58, 59))
    assert not in_session.unreachable
    in_session.check(_et(15, 59))
    assert in_session.unreachable_since == _et(15, 57)
    assert in_session.alert_text() == _IBKR_LOST_ALERT.format(since="15:57")

    # The supervisor's own bridge window, not a copy: 16:00 ET, 13:00 on a half day.
    assert late.bridge_ends_at == _bridge_window_end(SESSION) == _et(16, 0)
    half_day = date(2026, 11, 27)
    assert BrokerWatch(half_day).bridge_ends_at == _bridge_window_end(half_day)
    assert _bridge_window_end(half_day) == datetime(2026, 11, 27, 13, 0, tzinfo=MARKET_TIMEZONE)


def test_a_bridge_exit_makes_an_unrestored_loss_unknown_until_the_new_bridge_reports_it():
    """Review P1-A.1 L-2, in session: a bridge restarted mid-loss never hears the
    1102, so its predecessor's 1100 must not be re-asserted; a loss that is still
    there is the new client's to report (TWS 2110 on connect)."""
    from trading.operations.notify_service import BrokerWatch

    exit_line = "2026-10-13T15:01:00+00:00 CHILD_EXIT bridge code=1 restart_in=15"
    start_line = "2026-10-13T15:01:15+00:00 CHILD_START bridge pid=4242"

    restored = BrokerWatch(SESSION)
    restored.session_started(_et(7, 55))
    restored.observe("screener", _SWEEP_LINE, _et(11, 0))
    restored.observe("bridge", _BRIDGE_1100.replace("14:32:16", "11:00:30"), _et(11, 0, 30))
    # Another child's exit changes nothing; the bridge's makes the loss unknown.
    restored.observe("supervisor", exit_line.replace("bridge", "momo"), _et(11, 0, 50))
    assert restored.ibkr_lost_at == _et(11, 0, 30)
    restored.observe("supervisor", exit_line, _et(11, 1))
    assert restored.ibkr_lost_at is None
    restored.observe("supervisor", start_line, _et(11, 1, 15))
    for second in range(0, 600):
        assert restored.check(_et(11, 1, 16) + timedelta(seconds=second)) is None
    assert not restored.unreachable and not restored.hysteresis.is_open

    still_lost = BrokerWatch(SESSION)
    still_lost.session_started(_et(7, 55))
    still_lost.observe("screener", _SWEEP_LINE, _et(11, 0))
    still_lost.observe("bridge", _BRIDGE_1100.replace("14:32:16", "11:00:30"), _et(11, 0, 30))
    still_lost.observe("supervisor", exit_line, _et(11, 1))
    still_lost.observe("supervisor", start_line, _et(11, 1, 15))
    still_lost.observe("bridge", _BRIDGE_2110.replace("09:25:04", "11:01:20"), _et(11, 1, 20))
    still_lost.check(_et(11, 3, 19))
    assert not still_lost.unreachable
    # Re-asserted two minutes after the new client's 2110: the third failure in a row.
    still_lost.check(_et(11, 3, 20))
    assert still_lost.unreachable_since == _et(11, 0, 30)
    assert still_lost.alert_text() == _IBKR_LOST_ALERT.format(since="11:00")


def test_the_alert_wording_follows_the_newest_failure_and_pages_at_six_minutes_after_a_pair():
    """Review P1-A.1 L-3. Inside the half hour after an announced pair an IBKR
    loss is held back until its re-assertions (every 2 min) meet the 5-minute
    sustain limit: T+6 (OPERATOR_PROMPT 4.6). The wording names the newest failure."""
    from trading.operations.hysteresis import SUSTAIN_LIMIT
    from trading.operations.notify_service import BrokerWatch

    watch = BrokerWatch(SESSION)
    watch.session_started(_et(7, 55))
    watch.observe("screener", _SWEEP_LINE, _et(9, 59))
    for at in (_et(10, 0, 0), _et(10, 0, 20), _et(10, 0, 40)):
        watch.observe("screener", _SCREENER_FAILED, at)
    assert watch.alert_text() == _GATEWAY_ALERT.format(since="10:00")
    watch.alert_sent()
    for at in (_et(10, 1), _et(10, 2), _et(10, 2, 30)):  # three sweeps (P1-A.4)
        watch.observe("screener", _SWEEP_LINE, at)
    assert watch.check(_et(10, 3)) is not None and not watch.unreachable

    lost_at = _et(10, 10)
    watch.observe("bridge", _BRIDGE_1100.replace("14:32:16", "10:10:00"), lost_at)
    moment = lost_at + timedelta(seconds=1)
    while moment < lost_at + timedelta(minutes=6):
        watch.check(moment)
        assert not watch.unreachable, moment
        moment += timedelta(seconds=1)
    # Opened (held back by the pair budget) at T+2; still held at T+4.
    assert watch.hysteresis.is_open and watch.flaps_pending
    watch.check(lost_at + timedelta(minutes=6))
    assert SUSTAIN_LIMIT == timedelta(minutes=5)
    assert watch.unreachable_since == lost_at
    assert watch.alert_text() == _IBKR_LOST_ALERT.format(since="10:10")
    watch.alert_sent()
    # A failed connect is the Gateway itself not answering: "log in" again...
    watch.observe("screener", _SCREENER_FAILED, _et(10, 16, 30))
    assert watch.alert_text() == _GATEWAY_ALERT.format(since="10:10")
    # ...until the bridge's loss is re-asserted (it still has its connection),
    # or the bridge reports it again.
    watch.check(_et(10, 18))
    assert watch.alert_text() == _IBKR_LOST_ALERT.format(since="10:10")
    watch.observe("screener", _SCREENER_FAILED, _et(10, 18, 30))
    assert watch.alert_text() == _GATEWAY_ALERT.format(since="10:10")
    watch.observe("bridge", _BRIDGE_2110.replace("09:25:04", "10:19:00"), _et(10, 19))
    assert watch.alert_text() == _IBKR_LOST_ALERT.format(since="10:10")

    # A notifier restarted mid-session with no sweep yet: the first-sweep deadline
    # passes during a known IBKR loss, which explains it; the IBKR-side wording
    # stays, never "log in" (by 10:30 the loss has lasted 25 minutes, and its first
    # page went out at 10:07: a repeat says "restart now", P1-A.3).
    restarted = BrokerWatch(SESSION)
    restarted.session_started(_et(10, 0))
    restarted.observe("bridge", _BRIDGE_1100.replace("14:32:16", "10:05:00"), _et(10, 5))
    restarted.check(_et(10, 7))
    assert restarted.alert_text() == _IBKR_LOST_ALERT.format(since="10:05")
    restarted.alert_sent()
    restarted.check(_et(10, 30))
    assert restarted.first_sweep_overdue
    assert restarted.alert_text() == _RESTART_NOW_ALERT.format(since="10:05")


@pytest.mark.anyio
async def test_an_ibkr_loss_at_1559_sends_nothing_after_the_close(tmp_path, monkeypatch, caplog):
    """Review P1-A.1 L-2 through the notifier, until its 16:15 end: the bridge's
    1100 at 15:59, then the bridge and the screener stop at 16:00 and IBKR comes
    back at 16:00:30 with nobody to log it. No "unreachable" after 16:00."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(16, 15))
    logs = paths.log_directory
    clock = FakeTime(_et(15, 50))
    for n in range(8):
        clock.at(_et(15, 50, 30) + timedelta(seconds=65 * n), _append(logs / "screener.log", _SWEEP_LINE))
    clock.at(_et(15, 59), _append(logs / "bridge.log", _BRIDGE_1100.replace("14:32:16", "15:59:00")))
    clock.at(_et(16, 0), _append(
        logs / "supervisor.log",
        "2026-10-13T20:00:00+00:00 SIGNAL bridge SIGTERM reason=window_end",
        "2026-10-13T20:00:00+00:00 SIGNAL screener SIGTERM reason=window_end",
    ))
    clock.at(_et(16, 0, 2), _append(
        logs / "supervisor.log",
        "2026-10-13T20:00:02+00:00 CHILD_EXIT screener code=0 restart_in=none",
        "2026-10-13T20:00:02+00:00 CHILD_EXIT bridge code=0 restart_in=none",
    ))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    assert [text for text in telegram.sent() if text.startswith(("⛔ IB", "✅ IB"))] == []
    assert "broker_unreachable" not in _queued_alerts(caplog)
    # The bridge's own 1100 CRITICAL line still goes out, as before.
    assert sum(text.startswith("⚠️ CRITICAL TWS -1 1100") for text in telegram.sent()) == 1


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
    # A failed sweep every ~83 s, the last at 15:24:49 as live, before the
    # 15:24:53 restore (the cadence alone would put a 38th at 15:24:59, after it;
    # that matters since failed sweeps are failure signals, review P1-A.2 L-1).
    sweep_failed_at = [_et(14, 33, 48) + timedelta(seconds=83 * n) for n in range(37)]
    for at in [*sweep_failed_at, _et(15, 24, 49)]:
        clock.at(at, _append(
            screener, _SWEEP_FAILED, "2026-10-14 21:33:49,872 INFO screener connected to TWS"
        ))
    clock.at(_et(15, 24, 50), _append(logs / "bridge.log", _BRIDGE_1100.replace("14:32:16", "15:24:50")))
    clock.at(_et(15, 24, 53), _append(logs / "bridge.log", _BRIDGE_1102))
    for n in range(10):
        clock.at(_et(15, 26, 5) + timedelta(seconds=62 * n), _append(screener, _SWEEP_LINE))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    broker = [(at, text) for at, text in telegram.sent_at if text.startswith(("⛔ IB", "✅ IB"))]
    # The Gateway stayed logged in (the bridge kept its connection): the IBKR-side
    # wording, never "log in" (review P1-A.1 L-3); the half-hour repeat, 32 minutes
    # into the loss, says "restart IB Gateway now" (P1-A.3, GW-HMDS-1016); the same
    # recovery line follows, at the third success in a row (P1-A.4): the 15:24:53
    # restore, the 15:26:05 and 15:27:07 sweeps.
    assert [text for _at, text in broker] == [
        _IBKR_LOST_ALERT.format(since="14:32"),
        _RESTART_NOW_ALERT.format(since="14:32"),
        "✅ IB Gateway reconnected 15:24 ET — market data is back (unreachable since 14:32 ET)",
    ]
    assert _between(broker[0][0], _et(14, 34, 16))
    assert _between(broker[1][0], _et(15, 4, 16))
    assert _between(broker[2][0], _et(15, 27, 7))
    # The bridge's own 1100 CRITICAL lines still go out, as before.
    assert sum(text.startswith("⚠️ CRITICAL TWS -1 1100") for _at, text in telegram.sent_at) == 2


# --------------------------------------------------------------------------
# IBKR refusing data with the Gateway connected (GW-HMDS-1016, P1-A.3)
# --------------------------------------------------------------------------

GW_HMDS_DAY = date(2026, 10, 16)
_SYMBOLS = ("A", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "BF B", "BRK B", "MSFT")


def _et_on(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=MARKET_TIMEZONE).astimezone(
        timezone.utc
    )


def _screener_line(at: datetime, text: str) -> str:
    """A screener.log line as logged: the Mac's local time (Israel, ET+7 in October)."""
    local = at.astimezone(timezone(timedelta(hours=3)))
    return f"{local:%Y-%m-%d %H:%M:%S},000 {text}"


def _timeout_at(at: datetime, n: int) -> str:
    symbol = _SYMBOLS[n % len(_SYMBOLS)]
    return _screener_line(at, f"WARNING qualification of {symbol} failed: error: TWS daily-history request timed out")


def test_broker_evidence_reads_data_refused_with_the_gateway_connected():
    from trading.operations.notify_service import broker_evidence

    # One daily-history timeout is one piece of evidence; BrokerWatch counts the run.
    assert broker_evidence("screener", _HISTORY_TIMEOUT) == "history_timeout"
    assert broker_evidence("screener", _timeout_line("BF B")) == "history_timeout"
    # The day universe's own qualification line too (it runs between sweeps only).
    assert broker_evidence(
        "screener",
        "2026-10-16 15:00:20,048 WARNING day universe: qualification of XYZ failed:"
        " error: TWS daily-history request timed out",
    ) == "history_timeout"
    # A failed sweep (review P1-A.2 L-1), live 2026-10-14.
    assert broker_evidence("screener", _SWEEP_FAILED) == "sweep_failed"
    # The progress lines count attempts, not successes: neither failure nor success.
    for line in (
        _QUALIFIED_PROGRESS,
        "2026-10-16 15:00:00,037 INFO qualifying 503 of 503 instruments for 2026-10-16",
        "2026-10-16 16:59:17,504 INFO universe qualified for 2026-10-16: 388 qualified, 0 transient errors",
        # A contract's own rejection, another error, other timeouts, another sweep failure.
        "2026-10-16 15:00:20,048 WARNING qualification of XYZ failed: error: TWS error 200: No security definition",
        "2026-10-14 21:32:53,364 WARNING IBKR scan HOT_BY_VOLUME failed: TWS screener scan timed out",
        "2026-10-15 21:44:19,314 WARNING sweep snapshot errors (first 5): CPRT: TWS market snapshot timed out"
        " without prior close",
        "2026-10-16 15:00:20,048 ERROR screener sweep failed: no qualified instruments",
    ):
        assert broker_evidence("screener", line) is None, line
    # Screener lines only.
    assert broker_evidence("bridge", "09:00:00 ET  " + _HISTORY_TIMEOUT) is None
    assert broker_evidence("momo", _HISTORY_TIMEOUT) is None


def test_six_history_timeouts_in_a_run_are_one_failure_backdated_to_the_first():
    from trading.market.tws_actor import TWSActorConfig
    from trading.operations.hysteresis import State
    from trading.operations.notify_service import REFUSAL_GAP, REFUSAL_RUN, BrokerWatch

    assert REFUSAL_RUN == 6 and REFUSAL_GAP == timedelta(seconds=45)
    # Back-to-back timeouts, one per TWS request timeout, chain into one run.
    request_timeout = TWSActorConfig(account="DU1234567", client_id=1).request_timeout_seconds
    assert timedelta(seconds=request_timeout) == timedelta(seconds=20) < REFUSAL_GAP

    def watching() -> BrokerWatch:
        watch = BrokerWatch(SESSION)
        watch.session_started(_et(7, 55))
        watch.observe("screener", _SWEEP_LINE, _et(9, 59))
        return watch

    start = _et(10, 0, 20)
    watch = watching()
    for n in range(5):
        assert watch.observe("screener", _timeout_at(start, n), start + timedelta(seconds=20 * n)) is None
        # The progress line neither counts nor resets the run.
        watch.observe("screener", _QUALIFIED_PROGRESS, start + timedelta(seconds=20 * n + 1))
    assert watch.hysteresis.state is State.CLEAR and watch.refusal_count == 5
    # The sixth is one failure signal, backdated to the run's first timeout...
    watch.observe("screener", _timeout_at(start, 5), start + timedelta(seconds=100))
    assert watch.hysteresis.state is State.FAILING and watch.hysteresis.failing_since == start
    assert not watch.unreachable
    # ...and the seventh another: two minutes into the run, the alert opens.
    watch.observe("screener", _timeout_at(start, 6), start + timedelta(seconds=120))
    assert watch.unreachable_since == start
    assert watch.alert_text() == _DATA_REFUSED_ALERT.format(since="10:00")

    # Each within 45 s of the one before: one run (its sixth, backdated 3 min 45 s,
    # opens at once). 46 s: a new run each time, never a signal.
    chained, broken = watching(), watching()
    for n in range(6):
        chained.observe("screener", _timeout_at(start, n), start + timedelta(seconds=45 * n))
        broken.observe("screener", _timeout_at(start, n), start + timedelta(seconds=46 * n))
    assert chained.unreachable_since == start
    assert broken.hysteresis.state is State.CLEAR and broken.refusal_count == 1

    # A sweep between ends the run: five, a sweep, one more is a run of one.
    swept = watching()
    for n in range(5):
        swept.observe("screener", _timeout_at(start, n), start + timedelta(seconds=20 * n))
    swept.observe("screener", _SWEEP_LINE, start + timedelta(seconds=90))
    swept.observe("screener", _timeout_at(start, 5), start + timedelta(seconds=100))
    assert swept.hysteresis.state is State.CLEAR and swept.refusal_count == 1

    # Every failed sweep is a failure signal: the third opens it, worded "refusing prices".
    sweeps = watching()
    for at in (_et(10, 0), _et(10, 1, 23), _et(10, 2, 46)):
        sweeps.observe("screener", _SWEEP_FAILED, at)
    assert sweeps.unreachable_since == _et(10, 0)
    assert sweeps.alert_text() == _DATA_REFUSED_ALERT.format(since="10:00")


def test_the_alert_wording_switches_between_connect_failure_ibkr_loss_and_data_refused():
    """The opening names its cause; the newest failure names the alert (review
    P1-A.1 L-3); 15 minutes into an IBKR-side outage the advice is "restart now"."""
    from trading.operations.notify_service import RESTART_ADVICE_AFTER, BrokerWatch

    def watching() -> BrokerWatch:
        watch = BrokerWatch(SESSION)
        watch.session_started(_et(7, 55))
        watch.observe("screener", _SWEEP_LINE, _et(9, 59))
        return watch

    def refuse(watch: BrokerWatch, start: datetime, count: int = 7) -> None:
        for n in range(count):
            watch.observe("screener", _timeout_at(start, n), start + timedelta(seconds=20 * n))

    # Opened by failed connects: "log in", also 40 minutes on (a Gateway that
    # does not answer needs a login, not a restart).
    connect = watching()
    for at in (_et(10, 0, 0), _et(10, 0, 20), _et(10, 0, 40)):
        connect.observe("screener", _SCREENER_FAILED, at)
    assert connect.alert_text() == _GATEWAY_ALERT.format(since="10:00")
    connect.observe("screener", _SCREENER_FAILED, _et(10, 40))
    assert connect.alert_text() == _GATEWAY_ALERT.format(since="10:00")

    # Opened by TWS 2110 (the bridge connected, the Gateway cut off): the two steps.
    link = watching()
    link.observe("bridge", _BRIDGE_2110.replace("09:25:04", "10:00:00"), _et(10, 0))
    link.check(_et(10, 2))
    assert link.unreachable_since == _et(10, 0)
    assert link.alert_text() == _IBKR_LOST_ALERT.format(since="10:00")

    # Opened by data refused: its own headline, the same two steps in the same order.
    data = watching()
    refuse(data, _et(10, 0, 0))
    assert data.unreachable_since == _et(10, 0)
    assert data.alert_text() == _DATA_REFUSED_ALERT.format(since="10:00")
    # The newest failure names it: a failed connect, the run's next timeout, a
    # 2110, a failed sweep.
    data.observe("screener", _SCREENER_FAILED, _et(10, 2, 10))
    assert data.alert_text() == _GATEWAY_ALERT.format(since="10:00")
    data.observe("screener", _timeout_at(_et(10, 2, 40), 7), _et(10, 2, 40))
    assert data.alert_text() == _DATA_REFUSED_ALERT.format(since="10:00")
    data.observe("bridge", _BRIDGE_2110.replace("09:25:04", "10:03:00"), _et(10, 3))
    assert data.alert_text() == _IBKR_LOST_ALERT.format(since="10:00")
    data.observe("screener", _SWEEP_FAILED, _et(10, 3, 30))
    assert data.alert_text() == _DATA_REFUSED_ALERT.format(since="10:00")

    # 15 minutes into the outage a repeat of either IBKR-side wording says "restart
    # IB Gateway now" (time passing: the notifier's check every cycle); its first
    # page, sent at the opening, gave the two steps (review N14 LOW-1).
    assert RESTART_ADVICE_AFTER == timedelta(minutes=15)
    refused = watching()
    refuse(refused, _et(10, 0, 0))
    for watch, expected in ((refused, _DATA_REFUSED_ALERT), (link, _IBKR_LOST_ALERT)):
        watch.alert_sent()
        watch.check(_et(10, 14, 59))
        assert watch.alert_text() == expected.format(since="10:00")
        watch.check(_et(10, 15))
        assert watch.alert_text() == _RESTART_NOW_ALERT.format(since="10:00")
    link.observe("bridge", _BRIDGE_2110.replace("09:25:04", "10:16:00"), _et(10, 16))
    assert link.alert_text() == _RESTART_NOW_ALERT.format(since="10:00")
    # A failed connect inside it is the Gateway itself: "log in", whatever the age.
    link.observe("screener", _SCREENER_FAILED, _et(10, 17))
    assert link.alert_text() == _GATEWAY_ALERT.format(since="10:00")

    # The first-sweep grace runs out during a refusal before any sweep: the refusal
    # explains it, the wording stays (not "log in").
    morning = BrokerWatch(SESSION)
    morning.session_started(_et(7, 55))
    refuse(morning, _et(8, 25), count=16)
    assert morning.alert_text() == _DATA_REFUSED_ALERT.format(since="08:25")
    morning.check(_et(8, 30))
    assert morning.first_sweep_overdue
    assert morning.alert_text() == _DATA_REFUSED_ALERT.format(since="08:25")

    # No wording ever tells the owner to wait for a reconnect.
    for text in (_GATEWAY_ALERT, _IBKR_LOST_ALERT, _DATA_REFUSED_ALERT, _RESTART_NOW_ALERT):
        assert "reconnects by itself" not in text and "do not log in" not in text


@pytest.mark.anyio
async def test_owner_a_replay_of_the_1016_refusal_pages_data_refused_by_0803(tmp_path, monkeypatch, caplog):
    """Owner test (a), GW-HMDS-1016. Live 2026-10-16 the Gateway's port was open and
    the screener connected at 08:00 ET, but from 08:00:20 every universe
    qualification timed out, one line every ~20 s; "qualified 50/503" still came at
    08:16:40, and no sweep came until a Gateway restart at 09:51. 904af38 paged
    "unreachable since 07:55 ET — log in" at 08:15; P1-A.2 would have waited for the
    first-sweep grace (08:30) and said "log in" too."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(8, 40))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et_on(GW_HMDS_DAY, 7, 55))
    clock.at(_et_on(GW_HMDS_DAY, 7, 55, 1), _append(
        logs / "supervisor.log", "2026-10-16T11:55:00+00:00 SESSION_START date=2026-10-16"
    ))
    opened = _et_on(GW_HMDS_DAY, 8, 0)
    clock.at(opened, _append(
        screener,
        _screener_line(opened, "INFO screener connected to TWS"),
        _screener_line(opened, "INFO qualifying 503 of 503 instruments for 2026-10-16"),
    ))
    first = _et_on(GW_HMDS_DAY, 8, 0, 20)
    end = _et_on(GW_HMDS_DAY, 8, 40)
    timeouts = [first + timedelta(seconds=20 * n) for n in range(int((end - first) / timedelta(seconds=20)))]
    for n, at in enumerate(timeouts):
        lines = [_timeout_at(at, n)]
        if (n + 1) % 50 == 0:
            lines.append(_screener_line(at, f"INFO qualified {n + 1}/503 instruments"))
        clock.at(at, _append(screener, *lines))
    assert timeouts[49] == _et_on(GW_HMDS_DAY, 8, 16, 40)  # "qualified 50/503", as live
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=GW_HMDS_DAY, env=_environment(paths, telegram, clock)) == 0

    broker = [(at, text) for at, text in telegram.sent_at if text.startswith(("⛔ IB", "✅ IB"))]
    assert [text for _at, text in broker] == [
        _DATA_REFUSED_ALERT.format(since="08:00"),
        # The half-hour repeat, 32 minutes in: restart now.
        _RESTART_NOW_ALERT.format(since="08:00"),
    ]
    paged_at = broker[0][0]
    assert paged_at <= _et_on(GW_HMDS_DAY, 8, 3)
    # At the run's seventh timeout, two minutes after its first.
    assert _between(paged_at, _et_on(GW_HMDS_DAY, 8, 2, 20))
    assert _between(broker[1][0], _et_on(GW_HMDS_DAY, 8, 32, 20))
    # The 08:30 first-sweep grace changed nothing: no "log in", no extra page.
    assert not any("log in; the bot has no market data" in text for text in telegram.sent())
    assert _queued_alerts(caplog) == ["broker_unreachable", "broker_unreachable"]


# Offsets in seconds from 08:30:20 ET: the timeouts, then the sweeps after them.
_BLIPS = {
    "three timeouts": ([0, 20, 40], [140]),
    "five timeouts": ([0, 20, 40, 60, 80], [160]),
    # A sweep between (data flowed): two blips, not one run of ten.
    "five, a sweep, five": ([0, 20, 40, 60, 80, 120, 140, 160, 180, 200], [100, 250]),
    # A 50-second gap (some 30 healthy names): two runs of four.
    "four, a 50-second gap, four": ([0, 20, 40, 60, 110, 130, 150, 170], [250]),
    # Sporadic timeouts a minute apart: never a run.
    "eight, a minute apart": ([60 * n for n in range(8)], [460]),
}


@pytest.mark.anyio
@pytest.mark.parametrize("blip", sorted(_BLIPS))
async def test_owner_b_a_short_refusal_blip_that_recovers_by_itself_sends_nothing(
    blip, tmp_path, monkeypatch, caplog
):
    """Owner test (b): 3 to 5 daily-history timeouts, then a sweep within two
    minutes, send nothing; so do runs that a sweep or a long gap breaks up. The
    screener restarted at 08:30 and re-qualifies the names it lacks (as live at
    09:48 ET on 2026-10-16)."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(8, 45))
    screener = paths.log_directory / "screener.log"
    clock = FakeTime(_et(8, 25))
    for n in range(4):
        clock.at(_et(8, 26) + timedelta(seconds=65 * n), _append(screener, _SWEEP_LINE))
    clock.at(_et(8, 30), _append(
        screener,
        _screener_line(_et(8, 30), "INFO screener connected to TWS"),
        _screener_line(_et(8, 30), "INFO qualifying 40 of 503 instruments for 2026-10-13"),
    ))
    timeouts, sweeps = _BLIPS[blip]
    start = _et(8, 30, 20)
    for n, offset in enumerate(timeouts):
        at = start + timedelta(seconds=offset)
        clock.at(at, _append(screener, _timeout_at(at, n)))
    for offset in sweeps:
        clock.at(start + timedelta(seconds=offset), _append(screener, _SWEEP_LINE))
    after = start + timedelta(seconds=sweeps[-1])
    while (after := after + timedelta(seconds=65)) < _et(8, 45):
        clock.at(after, _append(screener, _SWEEP_LINE))
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    assert telegram.sent() == [startup_text(SESSION)]
    assert _queued_alerts(caplog) == []


@pytest.mark.anyio
@pytest.mark.parametrize("signal", ["history timeouts", "failed sweeps"])
async def test_owner_c_a_real_refusal_after_an_announced_blip_pages_within_the_sustain_limit(
    signal, tmp_path, monkeypatch, caplog
):
    """Owner test (c), the F-1 pattern (review P1-A: a real outage held 30 minutes
    behind an announced blip's pair). A refusal blip at 08:00 opens and is announced,
    a sweep at 08:04 recovers it (its pair goes out); IBKR refuses again from 08:10
    (re-qualification timeouts, or failed sweeps as on 2026-10-14). The page comes
    at the sustain limit (08:15), not when the pair budget frees (08:32)."""
    from datetime import time

    from trading.operations.hysteresis import SUSTAIN_LIMIT

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(8, 50))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et(7, 55))
    clock.at(_et(7, 55, 1), _append(logs / "supervisor.log", "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13"))
    blip = _et(8, 0, 20)
    for n in range(9):  # 08:00:20-08:03:00
        at = blip + timedelta(seconds=20 * n)
        clock.at(at, _append(screener, _timeout_at(at, n)))
    for n in range(6):  # the first sweep at 08:04, then every 65 s
        clock.at(_et(8, 4) + timedelta(seconds=65 * n), _append(screener, _SWEEP_LINE))
    refusal = _et(8, 10)
    timeouts = signal == "history timeouts"
    every = timedelta(seconds=20 if timeouts else 83)
    if timeouts:
        clock.at(refusal - timedelta(seconds=5), _append(
            screener, _screener_line(refusal, "INFO qualifying 37 of 503 instruments for 2026-10-13")
        ))

    def lines(at: datetime, n: int) -> tuple[str, ...]:
        if timeouts:
            return (_timeout_at(at, n),)
        return (_SWEEP_FAILED, _screener_line(at, "INFO screener connected to TWS"))

    at, n = refusal, 0
    while at < _et(8, 50):
        clock.at(at, _append(screener, *lines(at, n)))
        at, n = at + every, n + 1
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    broker = [(at, text) for at, text in telegram.sent_at if text.startswith(("⛔ IB", "✅ IB"))]
    assert [text for _at, text in broker] == [
        _DATA_REFUSED_ALERT.format(since="08:00"),
        "✅ IB Gateway reconnected 08:04 ET — market data is back (unreachable since 08:00 ET)",
        _DATA_REFUSED_ALERT.format(since="08:10"),
        _RESTART_NOW_ALERT.format(since="08:10"),
    ]
    # The blip's pair goes out at once (its recovery line at the third sweep, 08:06:10:
    # three in a row over two minutes, P1-A.4).
    assert _between(broker[0][0], _et(8, 2, 20)) and _between(broker[1][0], _et(8, 6, 10))
    # The real refusal pages at the first failure five minutes into its unbroken
    # run (08:15:00 for timeouts every 20 s, 08:15:32 for failed sweeps every 83 s),
    # not at 08:32:20 when the pair budget would free.
    assert SUSTAIN_LIMIT == timedelta(minutes=5)
    expected = _et(8, 15) if signal == "history timeouts" else _et(8, 15, 32)
    assert _between(broker[2][0], expected) and broker[2][0] <= _et(8, 16)
    assert broker[2][0] - refusal <= SUSTAIN_LIMIT + every + timedelta(seconds=5)
    # Its half-hour repeat, 35 minutes in: restart now.
    assert _between(broker[3][0], expected + timedelta(minutes=30))
    assert _queued_alerts(caplog) == [
        "broker_unreachable", "broker_reconnected", "broker_unreachable", "broker_unreachable",
    ]


@pytest.mark.anyio
async def test_owner_d_the_1013_slow_morning_without_timeouts_stays_silent(tmp_path, monkeypatch, caplog):
    """Owner test (d): live 2026-10-13 the screener qualified 503 names 08:00:00-08:08:24
    (a progress line every ~50 s, no timeout) and swept first at 08:10:02."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(8, 45))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et(7, 55))
    clock.at(_et(7, 55, 1), _append(logs / "supervisor.log", "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13"))
    clock.at(_et(8, 0), _append(
        screener,
        _screener_line(_et(8, 0), "INFO screener connected to TWS"),
        _screener_line(_et(8, 0), "INFO qualifying 503 of 503 instruments for 2026-10-13"),
    ))
    for n in range(1, 11):
        at = _et(8, 0) + timedelta(seconds=50 * n)
        clock.at(at, _append(screener, _screener_line(at, f"INFO qualified {50 * n}/503 instruments")))
    clock.at(_et(8, 8, 24), _append(screener, _screener_line(
        _et(8, 8, 24), "INFO universe qualified for 2026-10-13: 391 qualified, 0 transient errors"
    )))
    for n in range(26):
        clock.at(_et(8, 10, 2) + timedelta(seconds=80 * n), _append(screener, _SWEEP_LINE))
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    assert telegram.sent() == [startup_text(SESSION)]
    assert _queued_alerts(caplog) == []


# --------------------------------------------------------------------------
# P1-A.4: an announced outage clears only after several successes in a row,
# spread over minutes, never after a single one (owner 2026-10-17; review N14
# MEDIUM-1, LOW-1). Times are ET.
# --------------------------------------------------------------------------


def _broker_lines(telegram: TimedTelegram) -> list[tuple[datetime, str]]:
    """The Gateway alert's phone lines, with the simulated time each went out."""
    return [(at, text) for at, text in telegram.sent_at if text.startswith(("⛔ IB", "✅ IB"))]


def _broker_alerts_queued(caplog) -> list[str]:
    return [alert for alert in _queued_alerts(caplog) if alert.startswith("broker_")]


def _assert_lines(broker: list[tuple[datetime, str]], expected: list[tuple[datetime, str]]) -> None:
    """Exactly ``expected`` (due time, text), each sent from the notifier cycle at its due time."""
    assert [text for _at, text in broker] == [text for _due, text in expected]
    for (sent_at, text), (due, _text) in zip(broker, expected):
        assert _between(sent_at, due), (text, sent_at, due)


def _flaps_in_digests(telegram: FakeTelegram) -> int:
    return sum(
        int(match.group("count"))
        for text in telegram.sent()
        if text.startswith("🗂 Shadow digest")
        for match in map(_FLAP_LINE.match, text.splitlines()[1:])
        if match is not None
    )


def _sweep_on(day: date, at: datetime) -> str:
    """A screener sweep line as logged on ``day`` (any sweep is success evidence)."""
    return _screener_line(at, f"INFO sweep {day.isoformat()}: qualified=391 quoted=391 errors=0 hot=35 seconds=58.0")


_RECONNECTED = "✅ IB Gateway reconnected {back} ET — market data is back (unreachable since {since} ET)"


def test_p1a4_only_broker_watch_asks_for_three_successes_the_shared_default_stays_one():
    from trading.operations.hysteresis import CLOSE_AFTER, Hysteresis
    from trading.operations.notify_service import CLOSE_AFTER_COUNT, BrokerWatch

    assert CLOSE_AFTER_COUNT == 3
    machine = BrokerWatch(SESSION).hysteresis
    assert machine.close_after_count == CLOSE_AFTER_COUNT
    assert machine.close_after == CLOSE_AFTER == timedelta(minutes=2)
    # Every other number is the shared default, and the default count stays 1 for the
    # other users (plan HIGH-2: the overnight port watch, the competing-login watch).
    default = Hysteresis()
    assert default.close_after_count == 1
    names = ("open_after_count", "open_after", "close_after", "max_pairs_per_window", "window", "sustain_limit")
    assert [getattr(machine, name) for name in names] == [getattr(default, name) for name in names]
    assert BrokerWatch(SESSION).hysteresis is not machine


def test_p1a4_a_success_holds_a_repeat_back_only_until_the_next_failure():
    """Review N14 MEDIUM-1 at the unit: one sweep inside an announced outage set
    ``swept_since_alert`` and nothing cleared it when failures came back within
    two minutes, so ``alert_due`` stayed False for the rest of the outage."""
    from trading.operations.notify_service import BrokerWatch

    watch = BrokerWatch(SESSION)
    watch.session_started(_et(7, 55))
    watch.observe("screener", _SWEEP_LINE, _et(9, 59))
    for at in (_et(10, 0, 0), _et(10, 0, 20), _et(10, 0, 40)):
        watch.observe("screener", _SCREENER_FAILED, at)
    assert watch.unreachable and watch.alert_due  # the first page
    watch.alert_sent()
    assert watch.alert_due  # the next repeat, when the session's half-hour timer allows
    # One sweep: no repeat while it holds, and no close however long it holds.
    assert watch.observe("screener", _SWEEP_LINE, _et(10, 5)) is None
    assert watch.unreachable and not watch.alert_due
    for moment in (_et(10, 7), _et(10, 20), _et(10, 35)):
        assert watch.check(moment) is None
        assert watch.unreachable and not watch.alert_due
    # It did not hold: one failure, and the repeat is due again (a flap, no message).
    assert watch.observe("screener", _SCREENER_FAILED, _et(10, 35, 20)) is None
    assert watch.unreachable and watch.alert_due and watch.flaps_pending
    # Two sweeps over two minutes, then a failure: the same.
    watch.observe("screener", _SWEEP_LINE, _et(10, 36))
    watch.observe("screener", _SWEEP_LINE, _et(10, 37, 5))
    assert watch.check(_et(10, 38)) is None and not watch.alert_due
    assert watch.observe("screener", _SCREENER_FAILED, _et(10, 38, 10)) is None
    assert watch.unreachable and watch.alert_due
    # Three sweeps in a row, two clean minutes from the first: the recovery line.
    for at in (_et(10, 39), _et(10, 40, 5)):
        assert watch.observe("screener", _SWEEP_LINE, at) is None
    assert watch.check(_et(10, 41) - timedelta(seconds=1)) is None and watch.unreachable
    assert watch.observe("screener", _SWEEP_LINE, _et(10, 41, 10)) == _RECONNECTED.format(back="10:39", since="10:00")
    assert not watch.unreachable and not watch.alert_due


def test_p1a4_restart_now_is_never_an_outages_first_page():
    """Review N14 LOW-1 at the unit: the 08:30 backstop backdates the outage to
    08:00, so its first page already said "IBKR still refusing prices since 08:00 ET
    — restart IB Gateway now"."""
    from trading.operations.notify_service import RESTART_ADVICE_AFTER, BrokerWatch

    watch = BrokerWatch(SESSION)
    watch.session_started(_et(7, 55))
    start = _et(8, 20)
    for n in range(6):  # one failure signal, backdated to 08:20:00; no seventh
        watch.observe("screener", _timeout_at(start, n), start + timedelta(seconds=20 * n))
    assert not watch.unreachable
    watch.check(_et(8, 30))  # no first sweep by 08:30: open, failing since 08:00
    assert watch.unreachable_since == _et(8, 0)
    assert watch.checked_at - watch.unreachable_since >= RESTART_ADVICE_AFTER
    # Its first page: data refused, the two steps; never "restart now".
    assert watch.alert_due and watch.alert_text() == _DATA_REFUSED_ALERT.format(since="08:00")
    watch.alert_sent()
    # A repeat says it.
    watch.check(_et(9, 0))
    assert watch.alert_text() == _RESTART_NOW_ALERT.format(since="08:00")
    # A new outage's first page is a first page again.
    for at in (_et(9, 10), _et(9, 11, 5), _et(9, 12, 10)):
        watch.observe("screener", _SWEEP_LINE, at)
    assert not watch.unreachable
    restart = _et(9, 40)
    for n in range(7):
        watch.observe("screener", _timeout_at(restart, n), restart + timedelta(seconds=20 * n))
    watch.check(_et(10, 0))
    assert watch.unreachable_since == restart
    assert watch.alert_text() == _DATA_REFUSED_ALERT.format(since="09:40")


@pytest.mark.anyio
async def test_p1a4_low1_the_0830_first_page_after_one_refusal_signal_gives_the_two_steps(
    tmp_path, monkeypatch, caplog
):
    """Review N14 LOW-1 end to end: six daily-history timeouts 08:20:00-08:21:40 (one
    failure signal), a slow but healthy qualification, the first sweep at 08:36.
    a070848's first and only page, at 08:30: "IBKR still refusing prices since 08:00
    ET — restart IB Gateway now"."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(8, 45))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et(7, 55))
    clock.at(_et(7, 55, 1), _append(logs / "supervisor.log", "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13"))
    clock.at(_et(8, 0), _append(screener, _screener_line(_et(8, 0), "INFO screener connected to TWS")))
    start = _et(8, 20)
    for n in range(6):
        at = start + timedelta(seconds=20 * n)
        clock.at(at, _append(screener, _timeout_at(at, n)))
    for n in range(4):  # 08:36:00, 08:37:05, 08:38:10, 08:39:15
        clock.at(_et(8, 36) + timedelta(seconds=65 * n), _append(screener, _SWEEP_LINE))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    _assert_lines(_broker_lines(telegram), [
        (_et(8, 30), _DATA_REFUSED_ALERT.format(since="08:00")),
        (_et(8, 38, 10), _RECONNECTED.format(back="08:36", since="08:00")),
    ])
    assert not any("restart IB Gateway now" in text for text in telegram.sent())
    assert _broker_alerts_queued(caplog) == ["broker_unreachable", "broker_reconnected"]


# Owner test (a): live 2026-10-16 (GW-HMDS-1016) every universe qualification timed
# out, one line every ~20 s from 08:00:20 until the screener reconnected at 09:51
# (329 lines, max gap 41 s); the first sweep came at 10:00:36. ONE successful sweep
# is injected at about 08:40 ET.
_ONE_SWEEP = {
    # Between two timeouts, the 20-s cadence unbroken: the next run's sixth timeout,
    # 08:42:20 (1 min 50 s after the sweep), is the next failure signal.
    "between two timeouts": (_et_on(GW_HMDS_DAY, 8, 40, 30), None),
    # Then no timeout for 2 min 20 s (the screener sweeping, then qualifying again):
    # two clean minutes after ONE success; the next run's sixth timeout at 08:44:00.
    "then two clean minutes": (
        _et_on(GW_HMDS_DAY, 8, 40),
        (_et_on(GW_HMDS_DAY, 8, 40), _et_on(GW_HMDS_DAY, 8, 42, 20)),
    ),
}


@pytest.mark.anyio
@pytest.mark.parametrize("injected", sorted(_ONE_SWEEP))
async def test_p1a4_owner_a_one_sweep_inside_the_1016_refusal_keeps_the_repeats_and_restart_now(
    injected, tmp_path, monkeypatch, caplog
):
    """Owner test (a), P1-A.4. a070848 (review N14 MEDIUM-1): the sweep between two
    timeouts switched the half-hour repeats and "restart now" off for good (no page
    from 08:32 to 10:02); and a close on one success would have sent a false
    "reconnected 08:40" at 08:42 and paged a 'new' outage with the two steps again."""
    from datetime import time

    day = GW_HMDS_DAY
    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(10, 5))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et_on(day, 7, 55))
    clock.at(_et_on(day, 7, 55, 1), _append(
        logs / "supervisor.log", "2026-10-16T11:55:00+00:00 SESSION_START date=2026-10-16"
    ))
    opened = _et_on(day, 8, 0)
    clock.at(opened, _append(
        screener,
        _screener_line(opened, "INFO screener connected to TWS"),
        _screener_line(opened, "INFO qualifying 503 of 503 instruments for 2026-10-16"),
    ))
    sweep_at, pause = _ONE_SWEEP[injected]
    at, n = _et_on(day, 8, 0, 20), 0
    while at < _et_on(day, 9, 51):
        if pause is None or not pause[0] <= at < pause[1]:
            clock.at(at, _append(screener, _timeout_at(at, n)))
            n += 1
        at += timedelta(seconds=20)
    assert n >= 320  # as live: 329
    clock.at(sweep_at, _append(screener, _sweep_on(day, sweep_at)))
    # The real recovery: the first sweep at 10:00:36, then one about every minute.
    for k in range(5):
        back = _et_on(day, 10, 0, 36) + timedelta(seconds=65 * k)
        clock.at(back, _append(screener, _sweep_on(day, back)))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=day, env=_environment(paths, telegram, clock)) == 0

    _assert_lines(_broker_lines(telegram), [
        (_et_on(day, 8, 2, 20), _DATA_REFUSED_ALERT.format(since="08:00")),
        (_et_on(day, 8, 32, 20), _RESTART_NOW_ALERT.format(since="08:00")),
        # The injected sweep changes nothing on the phone: the half-hour repeats go
        # on, "restart IB Gateway now" included...
        (_et_on(day, 9, 2, 20), _RESTART_NOW_ALERT.format(since="08:00")),
        (_et_on(day, 9, 32, 20), _RESTART_NOW_ALERT.format(since="08:00")),
        # ...and the outage closes once, at the real recovery's third sweep (10:02:46,
        # 2 min 10 s after the first), never at ~08:42.
        (_et_on(day, 10, 2, 46), _RECONNECTED.format(back="10:00", since="08:00")),
    ])
    assert _broker_alerts_queued(caplog) == ["broker_unreachable"] * 4 + ["broker_reconnected"]
    # The lone sweep is one flap in a digest, not a message of its own.
    assert _flaps_in_digests(telegram) == 1


@pytest.mark.anyio
async def test_p1a4_a_relapse_20_s_after_the_recovery_sweep_keeps_the_repeats_1014_timing(
    tmp_path, monkeypatch, caplog
):
    """Review N14 MEDIUM-1 evidence 1, pinned as N14 asked: an announced Gateway
    outage from 14:20 whose first recovery sweep is 14:31:26; then, as live on
    2026-10-14, a failed sweep 20 s later (14:31:46), the bridge's TWS 1100 at
    14:32:16 and failed sweeps every ~83 s until the 15:24:53 restore. a070848: one
    page ("log in"), a flap line, then nothing until "reconnected" at 15:26:54."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(15, 40))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et(14, 10))
    for n in range(8):  # healthy: 14:11:00-14:18:35
        clock.at(_et(14, 11) + timedelta(seconds=65 * n), _append(screener, _SWEEP_LINE))
    # The Gateway stops answering at 14:20: the screener's reconnect attempts to 14:30.
    connects = [_et(14, 20, 0), _et(14, 20, 20), _et(14, 20, 40)]
    connects += [_et(14, 21, 50) + timedelta(seconds=70 * n) for n in range(8)]
    for at in connects:
        clock.at(at, _append(screener, _SCREENER_FAILED))
    clock.at(_et(14, 31, 26), _append(screener, _SWEEP_LINE))
    for at in [*(_et(14, 31, 46) + timedelta(seconds=83 * n) for n in range(38)), _et(15, 24, 49)]:
        clock.at(at, _append(screener, _SWEEP_FAILED))
    clock.at(_et(14, 32, 16), _append(logs / "bridge.log", _BRIDGE_1100))
    clock.at(_et(15, 24, 50), _append(logs / "bridge.log", _BRIDGE_1100.replace("14:32:16", "15:24:50")))
    clock.at(_et(15, 24, 53), _append(logs / "bridge.log", _BRIDGE_1102))
    for n in range(10):
        clock.at(_et(15, 26, 5) + timedelta(seconds=62 * n), _append(screener, _SWEEP_LINE))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    _assert_lines(_broker_lines(telegram), [
        (_et(14, 20, 40), _GATEWAY_ALERT.format(since="14:20")),
        # The sweep at 14:31:26 did not hold: the repeats come at their half hour,
        # IBKR-side and "restart now" (the newest failures are the failed sweeps
        # and the 1100's re-assertions).
        (_et(14, 50, 40), _RESTART_NOW_ALERT.format(since="14:20")),
        (_et(15, 20, 40), _RESTART_NOW_ALERT.format(since="14:20")),
        # The restore and the next two sweeps: three in a row, at the third.
        (_et(15, 27, 7), _RECONNECTED.format(back="15:24", since="14:20")),
    ])
    assert _broker_alerts_queued(caplog) == ["broker_unreachable"] * 3 + ["broker_reconnected"]


@pytest.mark.anyio
async def test_p1a4_a_tws_1102_inside_a_refusal_keeps_the_repeats_and_restart_now(tmp_path, monkeypatch, caplog):
    """Review N14 MEDIUM-1 evidence 4, pinned as N14 asked: IBKR's link back (the
    bridge's TWS 1102) while the price history is still refused, as could have
    happened on 2026-10-16 had a bridge been connected. a070848: the 08:02 page and
    a flap line; no 08:32 "restart now"."""
    from datetime import time

    day = GW_HMDS_DAY
    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(9, 5))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et_on(day, 7, 55))
    clock.at(_et_on(day, 7, 55, 1), _append(
        logs / "supervisor.log", "2026-10-16T11:55:00+00:00 SESSION_START date=2026-10-16"
    ))
    at, n = _et_on(day, 8, 0, 20), 0
    while at < _et_on(day, 9, 5):
        clock.at(at, _append(screener, _timeout_at(at, n)))
        at, n = at + timedelta(seconds=20), n + 1
    clock.at(_et_on(day, 8, 20, 5), _append(logs / "bridge.log", _BRIDGE_1102.replace("15:24:53", "08:20:05")))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=day, env=_environment(paths, telegram, clock)) == 0

    _assert_lines(_broker_lines(telegram), [
        (_et_on(day, 8, 2, 20), _DATA_REFUSED_ALERT.format(since="08:00")),
        (_et_on(day, 8, 32, 20), _RESTART_NOW_ALERT.format(since="08:00")),
        (_et_on(day, 9, 2, 20), _RESTART_NOW_ALERT.format(since="08:00")),
    ])
    assert _flaps_in_digests(telegram) == 1


# Owner test (b): offsets in seconds from 08:30:20 ET; t a daily-history timeout,
# c a failed connect, f a failed sweep, s a sweep. After the last one the screener
# qualifies again for five minutes (no line), then sweeps every 65 s.
_ONE_SUCCESS_BLIPS = {
    # The owner's words: three to five timeouts, then a sweep within two minutes.
    "three timeouts, one sweep": [(0, "t"), (20, "t"), (40, "t"), (100, "s")],
    "five timeouts, one sweep": [(0, "t"), (20, "t"), (40, "t"), (60, "t"), (80, "t"), (110, "s")],
    # A streak that never opened is broken by ONE success (only an announced outage
    # needs three), so a lone failure later starts afresh instead of opening it.
    "two failed connects, one sweep, a lone failed connect 10 min later": [
        (0, "c"), (20, "c"), (70, "s"), (670, "c"),
    ],
    "six timeouts (one signal), one sweep, a lone failed sweep 12 min later": [
        (0, "t"), (20, "t"), (40, "t"), (60, "t"), (80, "t"), (100, "t"), (110, "s"), (830, "f"),
    ],
}


@pytest.mark.anyio
@pytest.mark.parametrize("blip", sorted(_ONE_SUCCESS_BLIPS))
async def test_p1a4_owner_b_a_short_blip_that_recovers_by_itself_stays_quiet_on_one_sweep(
    blip, tmp_path, monkeypatch, caplog
):
    """Owner test (b), P1-A.4: a short self-recovering blip stays quiet, also when
    only one sweep follows it (P1-A.3's owner (b) has a sweep every 65 s after)."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(8, 55))
    screener = paths.log_directory / "screener.log"
    clock = FakeTime(_et(8, 25))
    for n in range(4):
        clock.at(_et(8, 26) + timedelta(seconds=65 * n), _append(screener, _SWEEP_LINE))
    start = _et(8, 30, 20)
    other = {"c": _SCREENER_FAILED, "f": _SWEEP_FAILED, "s": _SWEEP_LINE}
    for n, (offset, kind) in enumerate(_ONE_SUCCESS_BLIPS[blip]):
        at = start + timedelta(seconds=offset)
        clock.at(at, _append(screener, _timeout_at(at, n) if kind == "t" else other[kind]))
    after = start + timedelta(seconds=_ONE_SUCCESS_BLIPS[blip][-1][0] + 300)
    while after < _et(8, 55):
        clock.at(after, _append(screener, _SWEEP_LINE))
        after += timedelta(seconds=65)
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    assert telegram.sent() == [startup_text(SESSION)]
    assert _queued_alerts(caplog) == []


# Owner test (c), P1-A.4 (the F-1 case, and review N14's attack grid): a refusal blip
# from 08:00:20 is paged at 08:02:20; its recovery sweeps come every 65 s from 08:04:00
# until a second refusal starts R after that page: timeouts every 20 s, or failed
# sweeps every 83 s (as on 2026-10-14). With one or two recovery sweeps before it
# (R = 130 s, 190 s) a070848 never paged the failed-sweep relapse. Now the blip has
# not cleared (no ✅), so the relapse is the same outage: the phone still holds its
# alert and the 08:32:20 repeat says "restart now". After three sweeps (✅ at 08:06:10)
# the relapse is a new outage and pages at the sustain limit, never ~30 min late.
_HELD = [
    (_et(8, 2, 20), _DATA_REFUSED_ALERT.format(since="08:00")),
    (_et(8, 32, 20), _RESTART_NOW_ALERT.format(since="08:00")),
]
_CLOSED = [
    (_et(8, 2, 20), _DATA_REFUSED_ALERT.format(since="08:00")),
    (_et(8, 6, 10), _RECONNECTED.format(back="08:04", since="08:00")),
]
_RELAPSES = {
    ("130 s", "history timeouts"): _HELD,
    ("130 s", "failed sweeps"): _HELD,
    ("190 s", "history timeouts"): _HELD,
    ("190 s", "failed sweeps"): _HELD,
    # 08:07:20: timeouts from 08:07:20 page at 08:12:20 (5 min of unbroken failures,
    # the run backdated to its first); failed sweeps at 08:12:52 (the fifth, 5:32 in).
    ("5 min", "history timeouts"): [
        *_CLOSED,
        (_et(8, 12, 20), _DATA_REFUSED_ALERT.format(since="08:07")),
        (_et(8, 42, 20), _RESTART_NOW_ALERT.format(since="08:07")),
    ],
    ("5 min", "failed sweeps"): [
        *_CLOSED,
        (_et(8, 12, 52), _DATA_REFUSED_ALERT.format(since="08:07")),
        (_et(8, 42, 52), _RESTART_NOW_ALERT.format(since="08:07")),
    ],
    # 08:22:20: still inside the blip's half hour (to 08:32:20); the sustain limit pages it.
    ("20 min", "history timeouts"): [*_CLOSED, (_et(8, 27, 20), _DATA_REFUSED_ALERT.format(since="08:22"))],
    ("20 min", "failed sweeps"): [*_CLOSED, (_et(8, 27, 52), _DATA_REFUSED_ALERT.format(since="08:22"))],
}
_RELAPSE_AFTER = {"130 s": timedelta(seconds=130), "190 s": timedelta(seconds=190), "5 min": timedelta(minutes=5), "20 min": timedelta(minutes=20)}


@pytest.mark.anyio
@pytest.mark.parametrize("relapse, signal", sorted(_RELAPSES))
async def test_p1a4_owner_c_a_relapse_after_a_paged_blip_is_never_left_without_an_alert(
    relapse, signal, tmp_path, monkeypatch, caplog
):
    from datetime import time

    from trading.operations.hysteresis import SUSTAIN_LIMIT

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(8, 50))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et(7, 55))
    clock.at(_et(7, 55, 1), _append(logs / "supervisor.log", "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13"))
    blip = _et(8, 0, 20)
    for n in range(9):  # 08:00:20-08:03:00, paged at the seventh (08:02:20)
        at = blip + timedelta(seconds=20 * n)
        clock.at(at, _append(screener, _timeout_at(at, n)))
    second = _et(8, 2, 20) + _RELAPSE_AFTER[relapse]
    sweep = _et(8, 4)
    while sweep < second:
        clock.at(sweep, _append(screener, _SWEEP_LINE))
        sweep += timedelta(seconds=65)
    timeouts = signal == "history timeouts"
    every = timedelta(seconds=20 if timeouts else 83)
    at, n = second, 0
    while at < _et(8, 50):
        clock.at(at, _append(screener, _timeout_at(at, n) if timeouts else _SWEEP_FAILED))
        at, n = at + every, n + 1
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    broker = _broker_lines(telegram)
    _assert_lines(broker, _RELAPSES[relapse, signal])
    # Never left without an alert: when the relapse starts, the phone either still
    # holds the blip's alert (no ✅ before it, none after it while it fails) or had
    # its ✅, and then the relapse's own page comes within the sustain limit plus one
    # failure interval of its first failure.
    before = [text for sent_at, text in broker if sent_at < second]
    if before[-1].startswith("✅"):
        pages = [sent_at for sent_at, text in broker if sent_at >= second and text.startswith("⛔")]
        assert pages and pages[0] - second <= SUSTAIN_LIMIT + every + timedelta(seconds=5)
    else:
        assert not any(text.startswith("✅") for _at, text in broker)


# Owner test (d): a Gateway that does not answer from 10:00 (a failed connect every
# 20 s, paged at 10:00:40), then sweeps 65 s apart.
_OUTAGE = [_et(10, 0) + timedelta(seconds=20 * n) for n in range(30)]  # to 10:09:40
_RECOVERIES = {
    # Three sweeps in a row over two minutes: exactly one ✅, at the third (10:12:10),
    # not two minutes after the first (10:12:00).
    "three sweeps over two minutes": (
        _OUTAGE,
        [_et(10, 10) + timedelta(seconds=65 * n) for n in range(10)],
        (10, 20),
        [
            (_et(10, 0, 40), _GATEWAY_ALERT.format(since="10:00")),
            (_et(10, 12, 10), _RECONNECTED.format(back="10:10", since="10:00")),
        ],
    ),
    # Two sweeps, then a failure 2 min 10 s after the first: no ✅; the outage goes
    # on, its repeat at its half hour (10:30:40, not at the 10:12:10 relapse); then
    # the real recovery from 10:40.
    "two sweeps then a failure": (
        [*_OUTAGE, *(_et(10, 12, 10) + timedelta(seconds=20 * n) for n in range(84))],  # to 10:39:50
        [_et(10, 10), _et(10, 11, 5), *(_et(10, 40) + timedelta(seconds=65 * n) for n in range(5))],
        (10, 45),
        [
            (_et(10, 0, 40), _GATEWAY_ALERT.format(since="10:00")),
            (_et(10, 30, 40), _GATEWAY_ALERT.format(since="10:00")),
            (_et(10, 42, 10), _RECONNECTED.format(back="10:40", since="10:00")),
        ],
    ),
    # Two sweeps, a failure, one sweep, then eight minutes with no line (the screener
    # qualifying again): the failure restarted the count, so the 10:12 sweep is one,
    # not three; the ✅ comes at the third sweep after the failure (10:21:05).
    "after a failure the count starts again": (
        [*_OUTAGE, _et(10, 11, 30)],
        [_et(10, 10), _et(10, 11, 5), _et(10, 12), _et(10, 20), _et(10, 21, 5), _et(10, 22, 10)],
        (10, 25),
        [
            (_et(10, 0, 40), _GATEWAY_ALERT.format(since="10:00")),
            (_et(10, 21, 5), _RECONNECTED.format(back="10:12", since="10:00")),
        ],
    ),
}


@pytest.mark.anyio
@pytest.mark.parametrize("recovery", sorted(_RECOVERIES))
async def test_p1a4_owner_d_only_a_genuine_recovery_clears_with_exactly_one_reconnected_line(
    recovery, tmp_path, monkeypatch, caplog
):
    """Owner test (d), P1-A.4: CLOSE_AFTER_COUNT successes in a row over at least
    CLOSE_AFTER clear the alert with exactly one ✅; two successes then a failure do not."""
    from datetime import time

    failures, sweeps, until, expected = _RECOVERIES[recovery]
    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(*until))
    screener = paths.log_directory / "screener.log"
    clock = FakeTime(_et(9, 57))
    clock.at(_et(9, 58), _append(screener, _SWEEP_LINE))
    for at in failures:
        clock.at(at, _append(screener, _SCREENER_FAILED))
    for at in sweeps:
        clock.at(at, _append(screener, _SWEEP_LINE))
    telegram = TimedTelegram(clock)

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    _assert_lines(_broker_lines(telegram), expected)
    assert sum(text.startswith("✅") for _at, text in _broker_lines(telegram)) == 1
    assert _broker_alerts_queued(caplog)[-1] == "broker_reconnected"


@pytest.mark.anyio
async def test_p1a4_owner_e_the_1013_slow_morning_stays_silent(tmp_path, monkeypatch, caplog):
    """Owner test (e), P1-A.4: live 2026-10-13, one connect failure at the window
    open (08:00:10), a qualification of 503 names to 08:08:24 (a progress line every
    ~50 s, no timeout), the first sweep at 08:10:02, then a sweep every ~80 s with an
    isolated failed sweep at 08:25:42. Nothing reaches the phone."""
    from datetime import time

    paths = await _gateway_logs(tmp_path, monkeypatch, caplog, until=time(8, 45))
    logs = paths.log_directory
    screener = logs / "screener.log"
    clock = FakeTime(_et(7, 55))
    clock.at(_et(7, 55, 1), _append(logs / "supervisor.log", "2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13"))
    clock.at(_et(8, 0, 10), _append(screener, _SCREENER_FAILED))
    clock.at(_et(8, 0, 15), _append(
        screener,
        _screener_line(_et(8, 0, 15), "INFO screener connected to TWS"),
        _screener_line(_et(8, 0, 15), "INFO qualifying 503 of 503 instruments for 2026-10-13"),
    ))
    for n in range(1, 11):
        at = _et(8, 0, 15) + timedelta(seconds=50 * n)
        clock.at(at, _append(screener, _screener_line(at, f"INFO qualified {50 * n}/503 instruments")))
    clock.at(_et(8, 8, 24), _append(screener, _screener_line(
        _et(8, 8, 24), "INFO universe qualified for 2026-10-13: 391 qualified, 0 transient errors"
    )))
    for n in range(26):
        clock.at(_et(8, 10, 2) + timedelta(seconds=80 * n), _append(screener, _SWEEP_LINE))
    clock.at(_et(8, 25, 42), _append(screener, _SWEEP_FAILED))
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    assert telegram.sent() == [startup_text(SESSION)]
    assert _queued_alerts(caplog) == []


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
