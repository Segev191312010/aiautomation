"""Size changes on the owner's phone (plan P3-E, owner decision D7).

The replay child's ONE silent batch per night (each proposal id once, buttons
only once phone approval exists), the Telegram child's 05:55 ET reminder
(normal sound, only while a step up waits and only once approval exists) and
its 07:55 ET silent expiry note, Israel time computed for the real instant
(the clock-change weeks), the one sender's silent flag and keyboard, and the
size store's CRITICAL lines reaching the phone through the notifier. Every
Telegram call goes to a fake transport; nothing reads the Keychain.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from tests.test_nightly_evidence import night  # noqa: F401 - the fixture: the hermetic night
from tests.test_notifier import TOKEN, FakeTelegram, FakeTime, _error
from tests.test_overnight_watch import Port, Time, drive, make_watch
from tests.test_size_store import SizeLedger
from tests.test_size_writer import (
    FRIDAY,
    MONDAY,
    NEXT_MONDAY,
    TUESDAY,
    WEDNESDAY,
    database,  # noqa: F401 - the fixture: a migrated store that is the fence store's too
    et,
    run_night,
    start,
)
from trading.operations import sentinel, size_notify
from trading.operations.notifier import (
    TelegramBotAPI,
    TelegramNotifier,
    save_chat_id,
    telegram_config_file,
)
from trading.operations.paths import UserPaths
from trading.operations.size_notify import (
    EXPIRY_NOTE_GRACE,
    PHONE_APPROVAL_LIVE,
    ProposalWatch,
    approval_markup,
    deadline_text,
    night_items,
)
from trading.research import report_notify
from trading.research.report_notify import NOTHING_NEW, ReportNotifyEnvironment, notify_size_batch
from trading.storage.runtime_fence import SQLitePaperKillStore

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
CHAT = 42


def _env(tmp_path: Path, telegram: FakeTelegram, clock: FakeTime, now: datetime) -> ReportNotifyEnvironment:
    paths = UserPaths(home=tmp_path / "home")
    save_chat_id(telegram_config_file(paths), CHAT)
    return ReportNotifyEnvironment(
        paths=paths,
        read_token=lambda: TOKEN,
        transport=telegram.transport,
        clock=lambda: now,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def _sends(telegram: FakeTelegram) -> list[dict[str, Any]]:
    return [payload for _path, method, payload, _at in telegram.calls if method == "sendMessage"]


def _night_with_every_kind(database: Path, monkeypatch) -> None:
    """Monday night: ORB_V2 below the bottom rung (fenced), VWAP_PB_V2 steps down,
    GAP_GO_V2 is eligible for a step up."""
    monkeypatch.setattr(sentinel, "SENTINEL_FENCES_LIVE", True)
    run_night(
        database, et(MONDAY, 17, 40), fence_store=SQLitePaperKillStore(),
        ORB_V2="losing", VWAP_PB_V2="losing", GAP_GO_V2="strong",
    )


# --- the plan's tests -----------------------------------------------------------------


def test_phone_approval_live_is_false_until_p3d():
    """P3-D (``/approve``, the buttons' callback_query) is not built: nothing offers an approval."""
    from trading.operations import telegram_bot
    from trading.operations.notifier import TelegramBotAPI as API

    assert PHONE_APPROVAL_LIVE is False and size_notify.PHONE_APPROVAL_LIVE is False
    assert approval_markup(["0123456789"]) is None
    proposal = size_notify.SizeItem(
        proposal_id="0123456789", identity="ORB_V2", kind=size_notify.PROPOSAL, from_mult="0.25", to_mult="0.5",
        evidence="30 trades", nights=3, deadline=start(TUESDAY),
    )
    line = proposal.line()
    assert "approve" not in line.lower()
    assert "sizes stay unchanged: phone approval arrives later" in line
    assert "expires Tue 07:55 ET (14:55 Israel)" in line
    # Nobody listens for a button press yet: the assistant polls messages only.
    assert telegram_bot.ALLOWED_UPDATES == ("message", "edited_message")
    import inspect

    assert '"allowed_updates": ["message"]' in inspect.getsource(API.get_updates)


def test_batch_sent_once_silent_with_buttons(tmp_path, database, monkeypatch):
    _night_with_every_kind(database, monkeypatch)
    clock = FakeTime()
    telegram = FakeTelegram(clock)
    environment = _env(tmp_path, telegram, clock, et(MONDAY, 17, 45))

    assert notify_size_batch(database, MONDAY, env=environment) == report_notify.SENT

    (payload,) = _sends(telegram)
    assert payload["chat_id"] == CHAT
    assert payload["disable_notification"] is True  # silent (D7)
    assert "reply_markup" not in payload  # no buttons while phone approval does not exist
    lines = payload["text"].splitlines()
    items = night_items(database, MONDAY)
    assert [item.kind for item in items] == ["shadow_only", "step_down", "proposal"]
    assert lines[0] == "🌙 Sizes after the Mon 2026-10-19 session (silent; step-downs apply by themselves)"
    assert lines[1].startswith(f"SHADOW-ONLY ORB_V2 id {items[0].proposal_id}: ×0.25 → shadow-only: fenced")
    assert "resume is yours: autotraderctl paper resume --identity ORB_V2" in lines[1]
    assert lines[2].startswith(
        f"STEP DOWN VWAP_PB_V2 id {items[1].proposal_id}: ×0.5 → ×0.25 from the Tue 2026-10-20 session"
    )
    assert lines[3].startswith(f"PROPOSAL GAP_GO_V2 id {items[2].proposal_id}: ×0.5 → ×1; 30 trades, expectancy")
    assert lines[3].endswith(
        "evaluated 1 night since the last step; sizes stay unchanged: phone approval arrives later"
        " (this proposal expires Tue 07:55 ET (14:55 Israel))"
    )
    assert "approve" not in payload["text"].lower()

    # Once per proposal id: the record holds all three; a second run sends nothing.
    record = size_notify.delivery_state_file(environment.paths)
    assert record.stat().st_mode & 0o777 == 0o600
    assert set(size_notify.delivered_ids(record)) == {item.proposal_id for item in items}
    assert notify_size_batch(database, MONDAY, env=environment) == NOTHING_NEW
    assert len(_sends(telegram)) == 1

    # Phone approval live (P3-D's flip, patched here): the next night's step up carries
    # its Approve/Reject row, the "approve by" deadline, and is still silent.
    monkeypatch.setattr(size_notify, "PHONE_APPROVAL_LIVE", True)
    run_night(database, et(TUESDAY, 17, 40), fence_store=SQLitePaperKillStore(), VWAP_PB_V2="strong")
    assert notify_size_batch(database, TUESDAY, env=environment) == report_notify.SENT
    second = _sends(telegram)[-1]
    (vwap,) = night_items(database, TUESDAY)
    assert second["disable_notification"] is True
    assert second["reply_markup"] == {"inline_keyboard": [[
        {"text": f"Approve {vwap.proposal_id}", "callback_data": f"size_approve:{vwap.proposal_id}"},
        {"text": f"Reject {vwap.proposal_id}", "callback_data": f"size_reject:{vwap.proposal_id}"},
    ]]}
    assert second["text"].splitlines()[1].endswith("approve by Wed 07:55 ET (14:55 Israel)")
    # Monday's step up expired that night: a decided step up is no longer news.
    assert [item.kind for item in night_items(database, MONDAY)] == ["shadow_only", "step_down"]
    # A step down alone never carries a button, approval live or not.
    assert approval_markup(size_notify.proposal_ids(items[:2])) is None


def test_a_failed_batch_is_sent_by_the_next_run_and_a_night_without_news_reads_no_secret(
    tmp_path, database, monkeypatch
):
    _night_with_every_kind(database, monkeypatch)
    clock = FakeTime()
    telegram = FakeTelegram(clock, replies=[_error(502, "Bad Gateway")] * 3)
    environment = _env(tmp_path, telegram, clock, et(MONDAY, 17, 45))

    assert notify_size_batch(database, MONDAY, env=environment) == report_notify.FAILED
    assert size_notify.delivered_ids(size_notify.delivery_state_file(environment.paths)) == {}
    # The replay restarted: the writer writes nothing new, the batch is read back from the store.
    assert run_night(database, et(MONDAY, 18, 10)).changes == ()
    assert notify_size_batch(database, MONDAY, env=environment) == report_notify.SENT
    assert len({payload["text"] for payload in _sends(telegram)}) == 1

    def no_secret() -> str:
        raise AssertionError("a night without news must not read the token")

    quiet = ReportNotifyEnvironment(paths=environment.paths, read_token=no_secret, transport=telegram.transport)
    assert notify_size_batch(database, MONDAY, env=quiet) == NOTHING_NEW
    assert notify_size_batch(database, WEDNESDAY, env=quiet) == NOTHING_NEW


def test_the_nightly_job_hands_the_night_to_the_sender(night):  # noqa: F811
    from tests.test_nightly_evidence import TUESDAY as NIGHT_TUESDAY, _environment, _et
    from trading.research.replay import run_replay_cli

    handed: list[tuple[Path, date]] = []

    def sender(database: Path, evaluated: date) -> str:
        handed.append((database, evaluated))
        return report_notify.SENT

    environment = _environment(night, _et(NIGHT_TUESDAY, 17, 40))
    environment = type(environment)(
        database=environment.database, bridge_directory=environment.bridge_directory,
        clock=environment.clock, deliver_sizes=sender,
    )
    assert run_replay_cli([], environment=environment) == 0
    assert handed == [(Path(night["database"]).resolve(), NIGHT_TUESDAY)]
    sizes = json.loads((night["root"] / "reports" / "2026-10-12" / "evidence.json").read_text())["sizes"]
    assert sizes["delivery"] == "sent"
    # Production wires the replay child's own Telegram path.
    from trading.research import nightly

    assert nightly.NightlyEnvironment.__dataclass_fields__["deliver_sizes"].default is None
    import inspect

    assert "deliver_sizes=notify_size_batch" in inspect.getsource(nightly.NightlyEnvironment.current)


def _watch_run(tmp_path: Path, database: Path, begin: datetime, end: datetime, memory: dict[str, str]):
    clock = Time(begin, end)
    delivered: list[tuple[datetime, str, bool]] = []
    proposals = ProposalWatch(
        database=database, deliver=lambda text, silent: delivered.append((clock.now, text, silent))
    )
    watch, _ = make_watch(tmp_path, clock, Port(clock), memory=memory, proposals=proposals)
    sent = asyncio.run(drive(watch))
    return delivered, sent


def test_reminder_0555_et_normal_sound_only_if_pending(tmp_path, database, monkeypatch):
    run_night(database, et(MONDAY, 17, 40), ORB_V2="strong")  # pending, deadline Tuesday 07:55 ET
    (orb,) = night_items(database, MONDAY)
    monkeypatch.setattr(size_notify, "PHONE_APPROVAL_LIVE", True)
    memory: dict[str, str] = {}

    delivered, sent = _watch_run(tmp_path / "a", database, et(TUESDAY, 4, 0), et(TUESDAY, 7, 50), memory)

    (reminder,) = delivered
    assert reminder == (
        et(TUESDAY, 5, 55),
        f"⏰ Size proposal waiting for your approval: {orb.proposal_id} (ORB_V2 ×0.25 → ×0.5)."
        " Approve by Tue 07:55 ET (14:55 Israel), or the sizes stay unchanged.",
        False,  # normal sound
    )
    assert sent == []  # nothing on the watch's own channel: the port was open
    # A Telegram child restart after the lead: no second reminder.
    again, _sent = _watch_run(tmp_path / "b", database, et(TUESDAY, 6, 30), et(TUESDAY, 7, 50), memory)
    assert again == []

    # Nothing pending (the owner rejected it): no reminder.
    SizeLedger(database).decide(orb.proposal_id, "rejected", actor="owner_telegram", at=et(TUESDAY, 1, 0))
    none, _sent = _watch_run(tmp_path / "c", database, et(TUESDAY, 4, 0), et(TUESDAY, 7, 50), {})
    assert none == []


def test_no_reminder_and_no_expiry_note_while_phone_approval_does_not_exist(tmp_path, database):
    """P3-E fix 1 (open question 3): before P3-D a proposal cannot be approved, so
    neither the 05:55 reminder nor the 07:55 "expired" note is news."""
    run_night(database, et(MONDAY, 17, 40), ORB_V2="strong")
    memory: dict[str, str] = {}
    delivered, _sent = _watch_run(tmp_path, database, et(TUESDAY, 4, 0), et(TUESDAY, 9, 0), memory)
    assert delivered == [] and memory == {}


def test_expiry_note_0755_et(tmp_path, database, monkeypatch):
    # Rewritten for P3-E fix 1 (explicit, not weakened): the note exists only once phone
    # approval does (P3-D's flip, patched here); the not-live state is the test above.
    monkeypatch.setattr(size_notify, "PHONE_APPROVAL_LIVE", True)
    run_night(database, et(MONDAY, 17, 40), ORB_V2="strong", GAP_GO_V2="strong")
    orb, gap = night_items(database, MONDAY)
    # The 05:55 reminder went out already (its own test): only the deadline's note here.
    reminded = {size_notify.REMINDED_KEY: start(TUESDAY).astimezone(UTC).isoformat()}
    memory: dict[str, str] = dict(reminded)

    delivered, _sent = _watch_run(tmp_path / "a", database, et(TUESDAY, 7, 30), et(TUESDAY, 9, 30), memory)

    assert delivered == [(
        et(TUESDAY, 7, 55),
        f"⌛ Expired at Tue 07:55 ET (14:55 Israel), sizes unchanged: {orb.proposal_id} (ORB_V2 ×0.25 → ×0.5);"
        f" {gap.proposal_id} (GAP_GO_V2 ×0.5 → ×1)",
        True,  # silent
    )]
    # Once per deadline, across a restart inside the grace.
    again, _sent = _watch_run(tmp_path / "b", database, et(TUESDAY, 8, 0), et(TUESDAY, 9, 0), memory)
    assert again == []
    assert EXPIRY_NOTE_GRACE == timedelta(hours=1)
    # A proposal decided before the deadline is not "expired"; a closed day has no deadline.
    SizeLedger(database).decide(orb.proposal_id, "rejected", actor="owner_telegram", at=et(TUESDAY, 1, 0))
    later, _sent = _watch_run(tmp_path / "c", database, et(TUESDAY, 7, 50), et(TUESDAY, 8, 30), dict(reminded))
    assert [text.split(": ", 1)[1] for _at, text, _silent in later] == [f"{gap.proposal_id} (GAP_GO_V2 ×0.5 → ×1)"]
    assert ProposalWatch.schedule(et(date(2026, 10, 24), 7, 55)) is None
    # A store without migration 18 (the assistant tolerates it pending): no note, no failure.
    from tests.schema_support import database_at

    pending = database_at(tmp_path / "v17.db", 17)
    nothing, _sent = _watch_run(tmp_path / "d", pending, et(TUESDAY, 7, 50), et(TUESDAY, 8, 30), {})
    assert nothing == []


@pytest.mark.parametrize(
    ("evening", "deadline", "shown"),
    [
        # Israel left summer time on 25 Oct 2026, New York on 1 Nov: Israel is ET+6 that week.
        (date(2026, 10, 23), date(2026, 10, 26), "Mon 07:55 ET (13:55 Israel)"),
        (date(2026, 10, 19), date(2026, 10, 20), "Tue 07:55 ET (14:55 Israel)"),
        (date(2026, 11, 2), date(2026, 11, 3), "Tue 07:55 ET (14:55 Israel)"),
        # New York entered summer time on 14 Mar 2027, Israel on 26 Mar: ET+6 again.
        (date(2027, 3, 12), date(2027, 3, 15), "Mon 07:55 ET (13:55 Israel)"),
        (date(2027, 3, 5), date(2027, 3, 8), "Mon 07:55 ET (14:55 Israel)"),
        # Israel is back to ET+7 from 26 Mar 2027 (Good Friday: Thursday's night is for Monday).
        (date(2027, 3, 25), date(2027, 3, 29), "Mon 07:55 ET (14:55 Israel)"),
    ],
)
def test_deadline_israel_time_correct_in_dst_mismatch_week(tmp_path, database, evening, deadline, shown):
    run_night(database, et(evening, 17, 40), ORB_V2="strong")
    (item,) = night_items(database, evening)
    # The schedule is ET's: the deadline is the next session's 07:55 ET, whatever Israel's clock.
    assert item.deadline == start(deadline) == datetime.combine(deadline, time(7, 55), tzinfo=ET)
    assert deadline_text(item.deadline) == shown
    assert f"(this proposal expires {shown})" in item.line()
    reminder_at, due = ProposalWatch.schedule(et(deadline, 3, 0))
    assert (reminder_at, due) == (et(deadline, 5, 55), et(deadline, 7, 55))
    assert size_notify.expiry_text([], due).startswith(f"⌛ Expired at {shown}")


# --- the one sender ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_one_sender_sends_silent_and_carries_the_keyboard_on_the_last_part():
    clock = FakeTime()
    telegram = FakeTelegram(clock)
    keyboard = {"inline_keyboard": [[{"text": "Approve 0123456789", "callback_data": "size_approve:0123456789"}]]}
    long_text = "\n".join(f"PROPOSAL line {index:05d} with its evidence" for index in range(300))
    async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
        notifier = TelegramNotifier(api, chat_id=CHAT, sleep=clock.sleep, monotonic=clock.monotonic)
        results = await asyncio.gather(
            notifier.notify("plain"),
            notifier.send(long_text, silent=True, reply_markup=keyboard),
            notifier.send("silent only", silent=True),
        )
        await notifier.aclose()

    assert results == [True, True, True]
    payloads = _sends(telegram)
    assert payloads[0] == {"chat_id": CHAT, "text": "plain"}  # exactly as before: no option keys
    parts = payloads[1:-1]
    assert len(parts) > 1 and all(part["disable_notification"] is True for part in parts)
    assert [("reply_markup" in part) for part in parts] == [False] * (len(parts) - 1) + [True]
    assert parts[-1]["reply_markup"] == keyboard
    assert payloads[-1] == {"chat_id": CHAT, "text": "silent only", "disable_notification": True}


async def test_the_telegram_child_sends_the_expiry_note_silently_through_its_one_sender(tmp_path, monkeypatch):
    """Through run_telegram_assistant -> _serve: the composition root reads the
    configured database and queues the 07:55 ET note on its own sender, silent."""
    from tests.test_telegram_bot import OWNER, CeilingClock, env, fence_database, run_bounded
    from trading.operations import telegram_bot as bot
    from trading.operations.keychain import KeychainReadError

    database = await fence_database(tmp_path, monkeypatch)
    await asyncio.to_thread(run_night, database, et(MONDAY, 17, 40), ORB_V2="strong")
    (orb,) = night_items(database, MONDAY)
    begin = et(TUESDAY, 7, 50)
    clock = CeilingClock(begin, ceiling=begin + timedelta(minutes=6))
    sent: list[dict[str, Any]] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        if method == "sendMessage":
            sent.append(payload)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})
        await asyncio.sleep(0)
        if any(item["text"].startswith("⌛") for item in sent) or clock.stalled():
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        return httpx.Response(200, json={"ok": True, "result": []})

    async def opened() -> bool:
        return True

    def no_key() -> str:
        raise KeychainReadError("no key in this test")

    environment = env(
        tmp_path, transport=httpx.MockTransport(handle), read_anthropic_key=no_key,
        clock=clock.clock, sleep=clock.sleep, gateway_probe=opened,
    )
    save_chat_id(telegram_config_file(environment.paths), OWNER)
    # The note exists once phone approval does (P3-E fix 1): P3-D's flip, patched here.
    monkeypatch.setattr(size_notify, "PHONE_APPROVAL_LIVE", True)

    assert await run_bounded(bot.run_telegram_assistant(env=environment)) == bot.EXIT_CONFIG
    notes = [item for item in sent if item["text"].startswith("⌛")]
    assert notes == [{
        "chat_id": OWNER,
        "text": (
            f"⌛ Expired at Tue 07:55 ET (14:55 Israel), sizes unchanged: {orb.proposal_id}"
            " (ORB_V2 ×0.25 → ×0.5)"
        ),
        "disable_notification": True,
    }]


# --- the size store's CRITICAL lines reach the phone ---------------------------------------


def _formatted(records: list[logging.LogRecord]) -> list[str]:
    """Each record as the child's log file has it (the real formatter), line by line."""
    from trading.operations.logging import RedactingFormatter

    formatter = RedactingFormatter(secrets=[])
    lines: list[str] = []
    for record in records:
        lines += formatter.format(record).splitlines()
    return lines


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_size_criticals_reach_the_phone(tmp_path, monkeypatch):
    from tests.test_notify_manager import _session
    from tests.test_notify_service import FakeTime as NotifyTime, _et
    from trading.operations import notify_service
    from trading.orchestration.pipeline import ShadowPipeline
    from trading.risk import identity_sizing
    from trading.risk.engine import IDENTITY_SIZING_MISSING

    # The tokens: the size read's refusals, the ignored mid-session event, the pipeline's block.
    assert notify_service.SIZE_TRIPWIRES == (
        "identity_size_store_unreadable", "identity_size_above_ceiling", "account_mode_without_ceiling",
        "size_event_chain_broken", "size_event_written_mid_session", "size_read_before_session_start",
        "sendable_without_ceiling", "identity_sizing_missing", "size_writer_identity_skipped",
    )
    # Real CRITICAL lines, from the real code and the real log format.
    shadow = logging.getLogger("autotrader.shadow")
    capture = _Records()
    shadow.addHandler(capture)
    try:
        identity_sizing.session_identity_multiplier(
            "ORB_V2", sendable=True, session_date=TUESDAY, database=tmp_path / "missing.db",
            now=et(TUESDAY, 9, 40),
        )
        identity_sizing.session_identity_multiplier(
            "ORB_V2", sendable=True, session_date=TUESDAY, database=tmp_path / "missing.db",
            now=et(TUESDAY, 5, 0),
        )
        stand_in = SimpleNamespace(_identity_missing_reported=False)
        ShadowPipeline._report_identity_missing(  # type: ignore[arg-type]
            stand_in,
            strategy=SimpleNamespace(spec=SimpleNamespace(name="ORB_V2")),
            setup=SimpleNamespace(fingerprint=lambda: "f" * 64),
        )
    finally:
        shadow.removeHandler(capture)
    lines = _formatted(capture.records)
    assert sum(" CRITICAL " in line for line in lines) == 3 and len(lines) > 3  # stacks follow
    for token in notify_service.SIZE_TRIPWIRES[1:]:
        if token not in ("size_read_before_session_start", IDENTITY_SIZING_MISSING):
            lines.append(f"2026-10-20T09:40:00-0400 CRITICAL autotrader.shadow.sizing {token}: ORB_V2 has no size")
    # A decision line that merely names the block is no tripwire.
    decision = "2026-10-20T09:41:00-0400 INFO autotrader.shadow.pipeline BF B risk_blocked: identity_sizing_missing"

    clock = NotifyTime(_et(9, 40))
    session, notifier = _session(tmp_path, clock)

    async def feed() -> None:
        for line in [decision, *lines, *lines]:  # every line twice: once per day per token
            session._handle_line("orb-v2", line)
        await asyncio.sleep(0)
        session._collect_outcomes()
        for line in lines:  # after delivery, still once
            session._handle_line("orb-v2", line)

    asyncio.run(feed())

    heads = [text.splitlines()[0] for text in notifier.texts]
    assert sorted(heads) == sorted(f"🚨 Size tripwire: {token}" for token in notify_service.SIZE_TRIPWIRES)
    assert all(session._state.delivered[f"size_tripwire:{token}"] == "sent" for token in notify_service.SIZE_TRIPWIRES)
    unreadable = next(text for text in notifier.texts if "identity_size_store_unreadable" in text.splitlines()[0])
    assert "ORB_V2 has no size for the 2026-10-20 session" in unreadable
    # Normal sound: through notify(), the plain send (the fake has no silent path at all).
    assert not hasattr(notifier, "send")

    # The next session date's state: the same token reaches the phone again.
    from trading.operations.notify_service import NotifyState

    tomorrow = NotifyState(path=tmp_path / "notify-next.json", session_date=WEDNESDAY)
    session_next, notifier_next = _session(tmp_path / "next", clock, state=tomorrow)

    async def next_day() -> None:
        session_next._handle_line("orb-v2", lines[0])

    asyncio.run(next_day())
    heads_next = [text.splitlines()[0] for text in notifier_next.texts]
    assert heads_next == ["🚨 Size tripwire: identity_size_store_unreadable"]


def test_the_reminder_schedule_is_the_watchs_lead_and_the_sessions_start():
    from trading.operations.db_admin import trading_day
    from trading.operations.gateway_watch import PRE_SESSION_LEADS, pre_session_leads, session_start
    from trading.scheduler.sessions import market_session

    for day in (MONDAY, TUESDAY, FRIDAY, NEXT_MONDAY, date(2026, 11, 27)):
        session = market_session(day)
        assert session is not None
        reminder_at, deadline = ProposalWatch.schedule(et(day, 4, 0))
        # The deadline the writer stores is the instant the watch expires it at.
        assert deadline == session_start(day) == trading_day(session).starts_at
        # Two hours before it: the first pre-session lead (05:55 ET).
        assert reminder_at == pre_session_leads(day)[0] == deadline - PRE_SESSION_LEADS[0]
        assert reminder_at == deadline - timedelta(hours=2)


# --- P3-E fix 1: the size writer's skip reaches the phone from the replay log ----------------


def test_a_size_writer_skip_in_the_replay_log_reaches_the_phone_once_a_night(tmp_path, database):
    """The replay child's log line, as its console handler writes it, read by the
    Telegram child's overnight watch: one page per token per ET date, normal sound."""
    import inspect

    from tests.test_overnight_watch import append
    from trading import cli

    # The replay child's console format (cli._interactive_research_logger), copied, not guessed.
    replay_format = "%(asctime)s %(levelname)s %(message)s"
    assert f'logging.Formatter("{replay_format}")' in inspect.getsource(cli._interactive_research_logger)
    SizeLedger(database).step("ORB_V2", "0.5", "0.25", effective=MONDAY, at=et(date(2026, 10, 16), 20))
    capture = _Records()
    sizes = logging.getLogger("autotrader.shadow.sizes")
    sizes.addHandler(capture)
    try:
        run_night(database, et(MONDAY, 17, 40), VWAP_PB_V2="losing")
    finally:
        sizes.removeHandler(capture)
    formatter = logging.Formatter(replay_format)
    lines = [line for record in capture.records for line in formatter.format(record).splitlines()]
    assert sum(" CRITICAL size_event_chain_broken: ORB_V2 skipped" in line for line in lines) == 1

    clock = Time(et(MONDAY, 17, 30), et(MONDAY, 18, 30))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "replay.log").write_text("")
    memory: dict[str, str] = {}
    watch, _ = make_watch(tmp_path, clock, Port(clock), memory=memory)
    clock.at(et(MONDAY, 17, 41), append(logs / "replay.log", *lines))
    clock.at(et(MONDAY, 17, 50), append(logs / "replay.log", *lines))  # a restarted replay, the same night
    sent = asyncio.run(drive(watch))

    assert [text.splitlines()[0] for text in sent] == ["🚨 Size tripwire: size_event_chain_broken"]
    assert "ORB_V2 skipped by the nightly size writer" in sent[0]
    assert memory["replay_size_tripwire:size_event_chain_broken"] == "2026-10-19"
    # The next night it pages again.
    clock = Time(et(TUESDAY, 17, 30), et(TUESDAY, 18, 0))
    watch, _ = make_watch(tmp_path, clock, Port(clock), memory=memory)
    clock.at(et(TUESDAY, 17, 41), append(logs / "replay.log", *lines))
    assert len(asyncio.run(drive(watch))) == 1
