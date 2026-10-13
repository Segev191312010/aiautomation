"""A pending migration reaches the phone; no process migrates to make it go away."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
import re
import sqlite3

import httpx
import pytest

from tests.schema_support import apply_up_to
from tests.test_notify_service import (
    CHAT_ID,
    SESSION,
    TOKEN,
    FakeTelegram,
    FakeTime,
    _append,
    _environment,
    _et,
)
from trading.operations import notify_service
from trading.operations.keychain import KeychainReadError
from trading.operations.notifier import save_chat_id, telegram_config_file
from trading.operations.notify_service import (
    SCHEMA_CHECK_SECONDS,
    child_exit_code,
    run_notifier,
    startup_text,
)
from trading.operations.paths import UserPaths
from trading.operations.schema_gate import TELEGRAM_ASSISTANT_REQUIRES
from trading.operations.schema_alert import (
    alert_marker,
    claim_daily_alert,
    release_daily_alert,
    schema_alert_text,
    send_schema_alert,
)
from trading.storage.migrations import (
    MIGRATIONS,
    SCHEMA_CHECKSUM_MISMATCH,
    SCHEMA_UNREADABLE,
    SchemaProblem,
    SchemaStatus,
)

LATEST = MIGRATIONS[-1].version
PREVIOUS = LATEST - 1
KNOWN = tuple(migration.version for migration in MIGRATIONS)
PENDING = SchemaStatus(database="/data/autotrader.db", exists=True, applied=KNOWN[:-1], known=KNOWN)
CURRENT = SchemaStatus(database="/data/autotrader.db", exists=True, applied=KNOWN, known=KNOWN)


def _paths(tmp_path: Path) -> UserPaths:
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT_ID)
    return paths


def _token() -> str:
    return TOKEN


def _ledger(path: Path) -> list[int]:
    with sqlite3.connect(path) as db:
        return [
            row[0]
            for row in db.execute("SELECT version FROM autotrader_schema_migrations ORDER BY version")
        ]


async def _pending_database(tmp_path: Path, monkeypatch) -> Path:
    """The notifier's own database one migration behind (the real read-only check)."""
    from config import cfg

    path = await apply_up_to(tmp_path / "notify.db", PREVIOUS)
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    return path


async def _current_database(tmp_path: Path, monkeypatch) -> Path:
    from config import cfg
    from db.core import init_db

    path = tmp_path / "current.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


# --- the message and the once-per-day claim -------------------------------------------


def test_the_alert_names_the_command_the_versions_and_what_still_works():
    text = schema_alert_text(PENDING)

    assert text.startswith("⛔ Database needs migration — run `autotraderctl db migrate` after the close.")
    assert f"schema v{PREVIOUS}, this code needs v{LATEST} (pending: {LATEST})" in text
    assert "--during-session-i-know" in text and "autotraderctl paper stop" in text
    # Only a newer migration is pending: the assistant (and /stop) keeps working.
    assert "This chat (/stop, manual orders)" in text
    assert "the Telegram assistant refuse" not in text

    behind = replace(PENDING, applied=tuple(range(1, TELEGRAM_ASSISTANT_REQUIRES)))
    assert "the recorder and the Telegram assistant refuse to start" in schema_alert_text(behind)
    assert "This chat" not in schema_alert_text(behind)

    mismatch = replace(
        CURRENT,
        problems=(SchemaProblem(SCHEMA_CHECKSUM_MISMATCH, "database migration 3 checksum mismatch"),),
    )
    assert "does not match this code" in schema_alert_text(mismatch)
    assert "restore the pre-migration backup" in schema_alert_text(mismatch)
    unreadable = replace(
        CURRENT, problems=(SchemaProblem(SCHEMA_UNREADABLE, "database unreadable: DatabaseError"),)
    )
    assert schema_alert_text(unreadable).startswith("⛔ The database cannot be read")
    for status in (PENDING, mismatch, unreadable):
        assert "refuse to start (exit 75)" in schema_alert_text(status)
    for status in (mismatch, unreadable):
        assert "Nothing was migrated" in schema_alert_text(status)


def test_one_claim_per_day_and_a_failed_send_gives_it_back(tmp_path):
    paths = UserPaths(home=tmp_path / "home")
    day = date(2026, 10, 13)

    assert claim_daily_alert(paths, day) is True
    assert claim_daily_alert(paths, day) is False
    assert claim_daily_alert(paths, date(2026, 10, 14)) is True
    release_daily_alert(paths, day)
    assert claim_daily_alert(paths, day) is True
    assert alert_marker(paths, day).parent.stat().st_mode & 0o777 == 0o700


# --- the refusing Telegram assistant sends it directly --------------------------------


@pytest.mark.anyio
async def test_the_assistant_path_sends_once_per_day_to_the_pinned_owner(tmp_path):
    paths = _paths(tmp_path)
    telegram = FakeTelegram()

    first = await send_schema_alert(
        PENDING, paths=paths, now=_et(19, 0), read_token=_token, transport=telegram.transport
    )
    again = await send_schema_alert(
        PENDING, paths=paths, now=_et(19, 5), read_token=_token, transport=telegram.transport
    )

    assert (first, again) == (True, False)
    assert telegram.sent() == [schema_alert_text(PENDING)]
    assert [payload["chat_id"] for method, payload in telegram.calls if method == "sendMessage"] == [
        CHAT_ID
    ]


@pytest.mark.anyio
async def test_a_failed_send_is_retried_by_the_next_refusal(tmp_path):
    paths = _paths(tmp_path)
    outage = FakeTelegram(replies=[httpx.Response(502, json={"ok": False, "description": "down"})])

    assert not await send_schema_alert(
        PENDING, paths=paths, now=_et(19, 0), read_token=_token, transport=outage.transport
    )
    healthy = FakeTelegram()
    assert await send_schema_alert(
        PENDING, paths=paths, now=_et(19, 5), read_token=_token, transport=healthy.transport
    )
    assert healthy.sent() == [schema_alert_text(PENDING)]


@pytest.mark.anyio
async def test_no_alert_without_a_token_or_owner_and_none_for_a_current_schema(tmp_path):
    telegram = FakeTelegram()

    def missing() -> str:
        raise KeychainReadError("unable to read the requested Keychain secret")

    unpinned = UserPaths(home=tmp_path / "unpinned")
    assert not await send_schema_alert(
        PENDING, paths=unpinned, now=_et(19, 0), read_token=_token, transport=telegram.transport
    )
    assert not await send_schema_alert(
        PENDING, paths=_paths(tmp_path), now=_et(19, 0), read_token=missing, transport=telegram.transport
    )
    assert not await send_schema_alert(
        CURRENT, paths=_paths(tmp_path), now=_et(19, 0), read_token=_token, transport=telegram.transport
    )
    assert telegram.calls == []


# --- the notifier keeps running and tells the phone once ------------------------------


@pytest.mark.anyio
async def test_the_notifier_alerts_once_and_folds_the_exit_75_restarts(tmp_path, monkeypatch):
    database = await _pending_database(tmp_path, monkeypatch)
    paths = _paths(tmp_path)
    logs = paths.log_directory
    logs.mkdir(parents=True, exist_ok=True)
    clock = FakeTime(_et(16, 6))
    clock.at(_et(16, 6, 5), _append(
        logs / "supervisor.log",
        "2026-10-13T20:06:05+00:00 CHILD_EXIT screener code=75 restart_in=15",
        "2026-10-13T20:06:05+00:00 CHILD_EXIT momo code=75 restart_in=95",
        "2026-10-13T20:06:06+00:00 CHILD_EXIT orb code=1 restart_in=95",
    ))
    clock.at(_et(16, 7), _append(
        logs / "supervisor.log", "2026-10-13T20:07:00+00:00 CHILD_EXIT screener code=75 restart_in=30"
    ))
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    sent = telegram.sent()
    assert sent[0] == startup_text(SESSION)
    assert [text for text in sent if text.startswith("⛔")] == [schema_alert_text(PENDING)]
    assert "⚙️ orb exited (code 1), restarting in 95 s" in sent
    assert not any("code 75" in text for text in sent)
    # It read the older schema without trouble: the daily report went out.
    assert any(text.startswith("📊 Daily report") for text in sent)
    assert _ledger(database) == list(range(1, LATEST))  # nothing was migrated

    # A restart on the same session date does not repeat the alert.
    again = FakeTelegram()
    assert await run_notifier(
        session_date=SESSION, env=_environment(paths, again, FakeTime(_et(16, 10)))
    ) == 0
    assert not any(text.startswith("⛔") for text in again.sent())


@pytest.mark.anyio
async def test_with_a_current_schema_an_exit_75_is_reported_as_before(tmp_path, monkeypatch):
    await _current_database(tmp_path, monkeypatch)
    paths = _paths(tmp_path)
    logs = paths.log_directory
    logs.mkdir(parents=True, exist_ok=True)
    clock = FakeTime(_et(16, 6))
    clock.at(_et(16, 6, 5), _append(
        logs / "supervisor.log", "2026-10-13T20:06:05+00:00 CHILD_EXIT telegram code=75 restart_in=15"
    ))
    telegram = FakeTelegram()

    assert await run_notifier(session_date=SESSION, env=_environment(paths, telegram, clock)) == 0

    assert "⚙️ telegram exited (code 75), restarting in 15 s" in telegram.sent()
    assert not any(text.startswith("⛔") for text in telegram.sent())


@pytest.mark.anyio
async def test_a_migration_that_becomes_pending_reaches_the_phone_within_five_minutes(
    tmp_path, monkeypatch
):
    await _current_database(tmp_path, monkeypatch)
    paths = _paths(tmp_path)
    checks: list[float] = []
    clock = FakeTime(_et(16, 0))

    def read_status() -> SchemaStatus:
        checks.append(clock.monotonic())
        return CURRENT if len(checks) == 1 else PENDING

    telegram = FakeTelegram()
    environment = replace(_environment(paths, telegram, clock), read_schema_status=read_status)

    assert await run_notifier(session_date=SESSION, env=environment) == 0

    assert SCHEMA_CHECK_SECONDS == 300.0
    assert checks[1] - checks[0] <= SCHEMA_CHECK_SECONDS + 1
    assert [text for text in telegram.sent() if text.startswith("⛔")] == [schema_alert_text(PENDING)]


def test_child_exit_codes_are_parsed_from_supervisor_lines():
    assert child_exit_code("2026-10-13T11:55:01+00:00 CHILD_EXIT screener code=75 restart_in=15") == 75
    assert child_exit_code("2026-10-13T11:55:01+00:00 CHILD_START screener pid=7") is None


# --- the notifier needs no table a pending migration would add -----------------------


@pytest.mark.anyio
async def test_the_notifier_reads_only_tables_older_than_the_newest_migration(tmp_path, monkeypatch):
    from trading.market.context import latest_market_context
    from trading.market.screener import latest_hotlist

    database = await _pending_database(tmp_path, monkeypatch)
    with sqlite3.connect(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    source = Path(notify_service.__file__).read_text(encoding="utf-8")
    referenced = set(re.findall(r"\b(autotrader_[a-z0-9_]+)\b", source))

    assert referenced and referenced <= tables, sorted(referenced - tables)
    # The market readers it calls work on the older schema too.
    context = await latest_market_context(
        SESSION, now=_et(12, 0), max_age=notify_service.MARKET_CONTEXT_MAX_AGE
    )
    assert context is None
    assert tuple(await latest_hotlist(SESSION)) == ()
    assert await notify_service.load_signal_decisions(SESSION) == []
