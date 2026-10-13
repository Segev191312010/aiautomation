"""The learning loop's operations: research CLI, supervised commands, weekly Telegram summary."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, time
import json
import logging
import os
from pathlib import Path
import signal
import stat
import time as time_module
from typing import Any

import httpx
import pytest

from config import cfg
from trading import cli
from trading.operations import supervisor as supervisor_module
from trading.operations.keychain import KeychainReadError
from trading.operations.notifier import (
    TRUNCATION_MARKER,
    save_chat_id,
    telegram_config_file,
    token_secrets,
    utf16_length,
)
from trading.operations.paths import UserPaths
from trading.research import replay as replay_module
from trading.research import recorder as recorder_module
from trading.research.report_notify import (
    ALREADY_SENT,
    FAILED,
    NOT_CONFIGURED,
    NOT_DUE,
    NOT_REQUESTED,
    SENT,
    ReportNotifyEnvironment,
    deliver_summary,
    latest_closed_session,
    notify_summary,
    summary_message,
    weekly_state_file,
    weekly_summary_due,
)
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session

TOKEN = "123456789:AAH_test-token-value_0123456789abcdef"
CHAT_ID = 4242
MONDAY = date(2026, 10, 12)
TUESDAY = date(2026, 10, 13)
THURSDAY = date(2026, 10, 15)
FRIDAY = date(2026, 10, 16)
SUMMARY = (
    "Research replay 2026-09-15..2026-10-16: 22 sessions, 409 symbols\n"
    "Rule-only baseline: Claude review not simulated; synthetic one-tick quotes.\n\n"
    "MOMO (MOMO_HOD_V2)\n  31 trades from 2400 symbol-days\n\n"
    "GAP (GAP_GO_V1)\n  not replayed: needs extended bars"
)


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=MARKET_TIMEZONE)


@pytest.fixture(autouse=True)
def _shadow_logs_reach_caplog(monkeypatch, caplog):
    """configure_shadow_logging (run by other modules' CLI tests) stops the
    shadow logger propagating; the leak checks below must see every record."""
    monkeypatch.setattr(logging.getLogger("autotrader.shadow"), "propagate", True)
    caplog.set_level(logging.DEBUG, logger="autotrader.shadow")


# --- CLI ------------------------------------------------------------------------------


def test_research_cli_parses_backfill_record_and_replay():
    parser = cli._parser()

    backfill = parser.parse_args(["research", "backfill"])
    assert (backfill.domain, backfill.action, backfill.days) == ("research", "backfill", 21)
    assert parser.parse_args(["research", "backfill", "--days", "5"]).days == 5
    assert parser.parse_args(["research", "record"]).action == "record"
    replay = parser.parse_args(["research", "replay"])
    assert (replay.strategies, replay.notify) == (None, False)
    chosen = parser.parse_args(["research", "replay", "--strategy", "vwap", "gap", "--notify"])
    assert (chosen.strategies, chosen.notify) == (["vwap", "gap"], True)
    for invalid in (
        ["research"],
        ["research", "replay", "--strategy", "bogus"],
        ["research", "replay", "--strategy"],
        ["research", "backfill", "--days", "many"],
        ["research", "record", "--date", "2026-10-13"],  # records the session that just closed
    ):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(invalid)
        assert error.value.code == 2


def test_replay_strategy_choices_are_the_registered_strategies():
    replay = cli._parser().parse_args(["research", "replay", "--strategy", *cli._STRATEGY_CHOICES])

    assert tuple(replay.strategies) == replay_module.REGISTERED_STRATEGIES


def test_the_supervised_research_commands_parse_to_the_nightly_defaults():
    plans = {
        plan.name: plan
        for plan in supervisor_module.plan_session(market_session(TUESDAY))
        if plan.name in ("recorder", "replay")
    }
    parser = cli._parser()

    record = parser.parse_args(list(plans["recorder"].argv[2:]))
    replay = parser.parse_args(list(plans["replay"].argv[2:]))

    assert plans["recorder"].argv[1].endswith("autotraderctl.py")
    assert (record.domain, record.action) == ("research", "record")
    # No --notify: the summary goes out on the week's last session only.
    assert (replay.domain, replay.action, replay.strategies, replay.notify) == (
        "research", "replay", None, False,
    )


@pytest.fixture
def research_cli(monkeypatch):
    """Stub both research entry points; nothing connects, replays or logs to disk."""
    calls: list[tuple[str, Any]] = []
    returns = {"backfill": 0, "replay": 0}

    async def fake_backfill(days: int) -> int:
        calls.append(("backfill", days, cfg.DB_PATH))
        return returns["backfill"]

    def fake_replay(strategies, *, notify):
        calls.append(("replay", list(strategies), notify))
        return returns["replay"]

    monkeypatch.setattr(recorder_module, "run_backfill_cli", fake_backfill)
    monkeypatch.setattr(replay_module, "run_replay_cli", fake_replay)
    monkeypatch.setattr(
        cli, "_interactive_research_logger", lambda: logging.getLogger("autotrader.shadow.research")
    )
    # The schema gate has its own tests (test_schema_gate.py); here it passes.
    monkeypatch.setattr(cli, "require_current_schema", lambda **_kwargs: None)
    return calls, returns


def test_research_cli_runs_the_recorder_and_the_replay(research_cli):
    calls, returns = research_cli

    assert cli.main(["research", "backfill"]) == 0
    assert cli.main(["research", "backfill", "--days", "3"]) == 0
    assert cli.main(["research", "record"]) == 0
    assert cli.main(["research", "replay"]) == 0
    assert cli.main(["research", "replay", "--strategy", "vwap", "orb", "--notify"]) == 0
    returns.update(backfill=1, replay=2)
    assert cli.main(["research", "record"]) == 1
    assert cli.main(["research", "replay"]) == 2

    assert [call[:2] for call in calls if call[0] == "backfill"] == [
        ("backfill", 21), ("backfill", 3), ("backfill", 21), ("backfill", 21),
    ]
    assert [call for call in calls if call[0] == "replay"] == [
        ("replay", [], "weekly"),
        ("replay", ["vwap", "orb"], "now"),
        ("replay", [], "weekly"),
    ]


def test_research_cli_db_path_applies_to_the_universe_read_only(research_cli, tmp_path):
    calls, _returns = research_cli
    original = cfg.DB_PATH
    database = tmp_path / "copy.db"

    assert cli.main(["--db-path", str(database), "research", "record"]) == 0

    assert calls[-1] == ("backfill", 21, str(database))
    assert cfg.DB_PATH == original
    with pytest.raises(SystemExit) as error:
        cli.main(["--db-path", str(database), "research", "replay"])
    assert error.value.code == 2


def test_research_cli_reports_failures_without_raising(research_cli, monkeypatch, capsys):
    def broken(strategies, *, notify):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(replay_module, "run_replay_cli", broken)

    assert cli.main(["research", "replay"]) == 1
    assert "error: research replay failed: RuntimeError: store unreadable" in capsys.readouterr().err


def test_sigterm_stops_a_replay_in_order(research_cli, monkeypatch, capsys):
    cleaned: list[str] = []

    def slow_replay(strategies, *, notify):
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            time_module.sleep(30)
            return 0
        finally:
            cleaned.append("reports left consistent")

    monkeypatch.setattr(replay_module, "run_replay_cli", slow_replay)

    # Like every child, a window-end SIGTERM is an orderly stop (exit 0).
    assert cli.main(["research", "replay"]) == cli.EXIT_STOPPED == 0
    assert cleaned == ["reports left consistent"]
    assert "stopped by SIGTERM" in capsys.readouterr().err
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL


# --- weekly summary: when -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "due"),
    [
        (MONDAY, False),
        (TUESDAY, False),
        (THURSDAY, False),
        (FRIDAY, True),
        (date(2026, 10, 17), False),  # Saturday
        (date(2026, 11, 27), True),  # Friday half day after Thanksgiving
        (date(2026, 4, 2), True),  # Thursday before Good Friday
        (date(2026, 4, 3), False),  # Good Friday: no session
        (date(2026, 12, 24), True),  # Thursday half day before a Christmas Friday
        (date(2026, 12, 31), True),  # Thursday before a New Year's Day Friday
    ],
)
def test_the_weekly_summary_is_due_on_the_weeks_last_session(day, due):
    assert weekly_summary_due(day) is due


def test_latest_closed_session_follows_the_close_and_half_days():
    assert latest_closed_session(et(FRIDAY, 15, 59)) == THURSDAY
    assert latest_closed_session(et(FRIDAY, 16, 0)) == FRIDAY
    assert latest_closed_session(et(date(2026, 10, 17), 10)) == FRIDAY
    assert latest_closed_session(et(date(2026, 11, 27), 13)) == date(2026, 11, 27)


# --- weekly summary: delivery -----------------------------------------------------------------


class FakeTelegram:
    def __init__(self, reply: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.reply = reply

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((method, json.loads(request.content or b"{}")))
        if self.reply is not None:
            return self.reply(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.calls)}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def sent(self) -> list[dict[str, Any]]:
        return [payload for method, payload in self.calls if method == "sendMessage"]


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.mono = 1_000.0

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    async def sleep(self, seconds: float) -> None:
        self.mono += max(float(seconds), 0.0)
        await asyncio.sleep(0)


def _paths(tmp_path: Path, *, chat: bool = True) -> UserPaths:
    paths = UserPaths(home=tmp_path)
    if chat:
        save_chat_id(telegram_config_file(paths), CHAT_ID)
    return paths


def _env(
    paths: UserPaths,
    telegram: FakeTelegram,
    now: datetime,
    *,
    read_token: Callable[[], str] = lambda: TOKEN,
) -> ReportNotifyEnvironment:
    clock = FakeClock(now)
    return ReportNotifyEnvironment(
        paths=paths,
        read_token=read_token,
        transport=telegram.transport,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def _never_read() -> str:
    pytest.fail("the Keychain must not be read when no message is due")


async def test_the_weekly_summary_goes_out_once_on_friday_as_one_plain_message(tmp_path):
    paths = _paths(tmp_path)
    telegram = FakeTelegram()
    friday_evening = et(FRIDAY, 17, 40)

    outcome = await deliver_summary(
        SUMMARY, mode="weekly", newest_session=FRIDAY, env=_env(paths, telegram, friday_evening)
    )

    assert outcome == SENT
    (message,) = telegram.sent()
    assert message["chat_id"] == CHAT_ID
    assert "parse_mode" not in message  # plain text: underscores in codes survive
    lines = message["text"].splitlines()
    assert lines[0] == "📊 Weekly research summary · Fri 2026-10-16"
    assert lines[1] == ""  # tonight's session is stored: no warning
    assert message["text"].endswith(SUMMARY)
    assert [method for method, _ in telegram.calls] == ["sendMessage"]  # never getUpdates
    state = weekly_state_file(paths)
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert json.loads(state.read_text())["week"] == "2026-W42"

    # A supervisor restart reruns the replay the same evening: no second message.
    again = await deliver_summary(
        SUMMARY,
        mode="weekly",
        newest_session=FRIDAY,
        env=_env(paths, telegram, et(FRIDAY, 18, 5), read_token=_never_read),
    )
    assert again == ALREADY_SENT and len(telegram.sent()) == 1

    next_friday = await deliver_summary(
        SUMMARY,
        mode="weekly",
        newest_session=date(2026, 10, 23),
        env=_env(paths, telegram, et(date(2026, 10, 23), 17, 40)),
    )
    assert next_friday == SENT and len(telegram.sent()) == 2


async def test_weekly_mode_stays_silent_on_other_days_and_before_fridays_close(tmp_path):
    telegram = FakeTelegram()
    paths = _paths(tmp_path)
    moments = [et(day, 17, 40) for day in (MONDAY, TUESDAY, THURSDAY, date(2026, 10, 17))]
    # A hand-run replay on Friday morning must not use up the week's message
    # before Friday's session is recorded.
    moments.append(et(FRIDAY, 10, 0))

    for moment in moments:
        outcome = await deliver_summary(
            SUMMARY,
            mode="weekly",
            newest_session=THURSDAY,
            env=_env(paths, telegram, moment, read_token=_never_read),
        )
        assert outcome == NOT_DUE
    assert telegram.calls == []
    assert not weekly_state_file(paths).exists()
    assert await deliver_summary(SUMMARY, mode="never", newest_session=None) == NOT_REQUESTED


async def test_on_demand_sends_any_day_and_leaves_the_weekly_summary_due(tmp_path):
    paths = _paths(tmp_path)
    telegram = FakeTelegram()

    outcome = await deliver_summary(
        SUMMARY, mode="now", newest_session=MONDAY, env=_env(paths, telegram, et(TUESDAY, 9, 0))
    )

    assert outcome == SENT
    assert telegram.sent()[0]["text"].startswith("📊 Research summary · Tue 2026-10-13\n\n")
    assert not weekly_state_file(paths).exists()
    friday = await deliver_summary(
        SUMMARY, mode="weekly", newest_session=FRIDAY, env=_env(paths, telegram, et(FRIDAY, 17, 40))
    )
    assert friday == SENT


async def test_a_session_missing_from_the_store_is_flagged_in_the_message(tmp_path):
    telegram = FakeTelegram()

    await deliver_summary(
        SUMMARY,
        mode="weekly",
        newest_session=THURSDAY,
        env=_env(_paths(tmp_path), telegram, et(FRIDAY, 17, 40)),
    )

    assert telegram.sent()[0]["text"].splitlines()[1] == (
        "⚠️ The research store ends at 2026-10-15: 2026-10-16 was not recorded"
        " (see recorder.log)."
    )


def test_an_oversized_summary_is_still_exactly_one_message():
    text = summary_message(
        "x" * 3000 + "\n" + "y" * 3000, now=et(FRIDAY, 17, 40), newest_session=None, weekly=True
    )

    assert utf16_length(text) <= 4096
    assert text.endswith(TRUNCATION_MARKER)
    assert "The research store ends at no session" in text
    assert not text.startswith("(1/")


@pytest.mark.parametrize(
    "setup",
    ["keychain_empty", "token_malformed", "no_pinned_chat", "chat_file_corrupt"],
)
async def test_telegram_that_is_not_set_up_is_skipped_silently(tmp_path, capsys, setup):
    paths = _paths(tmp_path, chat=setup not in ("no_pinned_chat", "chat_file_corrupt"))
    read_token: Callable[[], str] = lambda: TOKEN
    if setup == "keychain_empty":

        def read_token() -> str:
            raise KeychainReadError("unable to read the requested Keychain secret")

    elif setup == "token_malformed":
        read_token = lambda: "not-a-token"  # noqa: E731
    elif setup == "chat_file_corrupt":
        config = telegram_config_file(paths)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("{not json")
    telegram = FakeTelegram()

    outcome = await deliver_summary(
        SUMMARY,
        mode="weekly",
        newest_session=FRIDAY,
        env=_env(paths, telegram, et(FRIDAY, 17, 40), read_token=read_token),
    )

    assert outcome == NOT_CONFIGURED
    assert telegram.calls == []  # in particular, no getUpdates: the assistant owns it
    assert capsys.readouterr() == ("", "")
    assert not weekly_state_file(paths).exists()  # Friday stays due once it is set up


def _assert_no_token(text: str) -> None:
    for secret in token_secrets(TOKEN):
        assert secret not in text


def test_the_bot_token_never_leaks_from_a_failed_send(tmp_path, monkeypatch, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    paths = _paths(tmp_path)
    leaky = FakeTelegram(
        reply=lambda request: httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": f"Unauthorized {TOKEN}"}
        )
    )

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    down = FakeTelegram(reply=unreachable)

    for telegram in (leaky, down):
        outcome = notify_summary(
            SUMMARY, mode="now", newest_session=FRIDAY, env=_env(paths, telegram, et(FRIDAY, 17, 40))
        )
        assert outcome == FAILED
        assert telegram.calls  # it really tried
    # Through the replay CLI as well: its error lines must not carry it either.
    monkeypatch.setattr(
        ReportNotifyEnvironment,
        "current",
        classmethod(lambda cls: _env(paths, leaky, et(FRIDAY, 17, 40))),
    )
    root = tmp_path / "research"
    monkeypatch.setattr(
        replay_module.ResearchPaths, "default", classmethod(lambda cls: cls(root=root))
    )
    _store_one_session(root)
    assert replay_module.run_replay_cli(["orb"], notify="now") == 1

    output = capsys.readouterr()
    _assert_no_token(output.out + output.err)
    _assert_no_token("\n".join(record.getMessage() for record in caplog.records))
    _assert_no_token(caplog.text)
    assert "summary NOT sent" in output.err
    assert not weekly_state_file(paths).exists()


def _store_one_session(root: Path) -> None:
    """One tiny stored session, enough for run_replay_cli to proceed."""
    from decimal import Decimal

    from trading.research.bars_store import BarsStore, ResearchPaths, StoredBar, StoredDay

    start = et(FRIDAY, 9, 30)
    BarsStore(ResearchPaths(root)).write_day(
        StoredDay(
            session_date=FRIDAY,
            con_id=101,
            symbol="AAA",
            bars=(
                StoredBar(
                    interval_start_utc=start,
                    open=Decimal("50"),
                    high=Decimal("50.5"),
                    low=Decimal("49.5"),
                    close=Decimal("50.2"),
                    volume=Decimal("1000"),
                ),
            ),
        )
    )


async def test_an_unreadable_weekly_state_fails_open(tmp_path, caplog):
    paths = _paths(tmp_path)
    state = weekly_state_file(paths)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{garbage")
    telegram = FakeTelegram()

    outcome = await deliver_summary(
        SUMMARY, mode="weekly", newest_session=FRIDAY, env=_env(paths, telegram, et(FRIDAY, 17, 40))
    )

    # At worst one duplicate message; never a silenced week.
    assert outcome == SENT and len(telegram.sent()) == 1
    assert json.loads(state.read_text())["week"] == "2026-W42"
    assert "ignoring telegram-weekly.json" in caplog.text


def test_unknown_modes_are_refused():
    with pytest.raises(ValueError, match="unknown notify mode"):
        notify_summary(SUMMARY, mode="daily", newest_session=None)  # type: ignore[arg-type]
