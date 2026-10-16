"""The owner's Telegram assistant: pinning, dedupe, staleness, commands,
orders through the desk only, read-only Claude answers, and startup refusals."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import io
import json
import logging
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.operations import telegram_bot as bot
from trading.operations.notifier import TelegramAPIError, save_chat_id, telegram_config_file
from trading.operations.paths import UserPaths
from trading.operations.telegram_bot import (
    BUDGET_NOTICE,
    CHAT_MODEL,
    EDIT_NOTICE,
    AssistantEnvironment,
    AssistantState,
    AuditLog,
    ChatMemory,
    ChatResponder,
    Inbound,
    OrderView,
    ParsedText,
    Submission,
    TelegramAssistant,
    Turn,
    classify_update,
    queued_text,
)
from trading.operations.telegram_tools import TOOL_DEFINITIONS, ToolError

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
OWNER = 4242
STRANGER = 9999
NOW = datetime(2026, 10, 13, 10, 30, tzinfo=ET).astimezone(UTC)
TOKEN = "123456789:AAH_test-token-value_0123456789abcdef"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeTime:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.mono = 1_000.0
        self.sleeps: list[float] = []

    def clock(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.mono += seconds
        self.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class FakeUpdates:
    def __init__(self, batches: Sequence[Sequence[Mapping[str, Any]] | BaseException] = ()) -> None:
        self.batches = list(batches)
        self.offsets: list[int | None] = []

    async def get_updates(self, offset: int | None) -> list[Mapping[str, Any]]:
        self.offsets.append(offset)
        if not self.batches:
            return []
        batch = self.batches.pop(0)
        if isinstance(batch, BaseException):
            raise batch
        return list(batch)


class FakeReplies:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def notify(self, text: str) -> asyncio.Future[bool]:
        self.texts.append(text)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        future.set_result(True)
        return future

    async def aclose(self, *, drain_timeout: float = 15.0) -> None:
        pass


class FakeTools:
    tws = None

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.quotes: list[str] = []

    async def _text(self, name: str) -> str:
        self.calls.append(name)
        return f"{name} text"

    async def status(self) -> str:
        return await self._text("status")

    async def positions(self) -> str:
        return await self._text("positions")

    async def pnl(self) -> str:
        return await self._text("pnl")

    async def orders(self) -> str:
        return await self._text("orders")

    async def hotlist(self) -> str:
        return await self._text("hotlist")

    async def market(self) -> str:
        return await self._text("market")

    async def signals(self) -> str:
        return await self._text("signals")

    async def report(self) -> str:
        return await self._text("report")

    async def fences(self) -> str:
        return await self._text("fences")

    async def quote(self, symbol: str) -> str:
        self.quotes.append(symbol)
        return f"quote {symbol}"


ORDER_ID = "a1b2c3" + "0" * 26


def view(status: str, **fields: str) -> OrderView:
    base = {
        "order_id": ORDER_ID,
        "short_id": "a1b2c3",
        "status": status,
        "side": "BUY",
        "qty": "100",
        "symbol": "AAPL",
        "order_type": "MKT",
    }
    base.update(fields)
    return OrderView(**base)


class FakeDesk:
    """Parser by table; submit scripted; rows evolve when the test says so."""

    def __init__(self) -> None:
        self.recorded: list[int] = []
        self.inbounds: list[Inbound] = []
        self.outcomes: dict[int, str] = {}
        self.parsed: list[str] = []
        self.parse_table: dict[str, ParsedText] = {}
        self.exits: set[str] = set()
        self.submitted: list[tuple[ParsedText, Inbound]] = []
        self.rejection: tuple[str, str] | None = None
        self.queued = view("queued")
        self.rows: dict[str, OrderView] = {}
        self.today: list[OrderView] = []
        self.record_error: BaseException | None = None
        self.row_queries: list[list[str]] = []

    async def check_ready(self) -> None:
        pass

    async def record_update(self, inbound: Inbound, *, received_at: datetime) -> bool:
        if self.record_error is not None:
            raise self.record_error
        if inbound.update_id in self.recorded:
            return False
        self.recorded.append(inbound.update_id)
        self.inbounds.append(inbound)
        return True

    async def mark_processed(self, update_id: int, outcome: str, *, at: datetime) -> None:
        self.outcomes.setdefault(update_id, outcome)

    def is_kill_switch(self, text: str) -> bool:
        words = text.split()
        return len(words) == 1 and words[0].split("@")[0].lower() == "/stop"

    def is_exit(self, text: str) -> bool:
        return text in self.exits

    def parse(self, text: str) -> ParsedText:
        self.parsed.append(text)
        return self.parse_table.get(text, ParsedText("not_order"))

    async def submit(self, parsed, inbound, *, chat_id, now) -> Submission:
        self.submitted.append((parsed, inbound))
        if self.rejection is not None:
            code, message = self.rejection
            return Submission(False, bot.rejected_text(code, message), code=code)
        self.rows[self.queued.order_id] = self.queued
        return Submission(True, queued_text(self.queued), order_id=self.queued.order_id, watch=(self.queued,))

    async def orders(self, order_ids):
        self.row_queries.append(list(order_ids))
        return [self.rows[order_id] for order_id in order_ids if order_id in self.rows]

    async def todays_orders(self, now):
        return list(self.today)


class FakeKillSwitch:
    def __init__(self) -> None:
        self.stopped = False
        self.reasons: list[str] = []

    async def is_stopped(self) -> bool:
        return self.stopped

    async def stop(self, *, reason: str) -> None:
        self.reasons.append(reason)
        self.stopped = True


class FakeFences:
    """The identity rows of SQLitePaperKillStore: what /confirm writes, and nothing else."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.rows: dict[str | None, str] = {}
        self.error: BaseException | None = None
        self.takes = True

    async def stop(self, *, reason: str, identity: str | None = None) -> None:
        self.calls.append((reason, identity))
        if self.error is not None:
            raise self.error
        if self.takes:
            self.rows[identity] = reason

    async def is_stopped(self, *, identity: str | None = None) -> bool:
        return identity in self.rows


NONCES = ("a1b2c3", "d4e5f6", "c0ffee", "0badf0")


class FakeQuestions:
    def __init__(self, answer: str = "Here is the answer.", error: BaseException | None = None) -> None:
        self.asked: list[tuple[str, int | None]] = []
        self.answer_text = answer
        self.error = error

    async def answer(self, question: str, *, message_id: int | None) -> str:
        self.asked.append((question, message_id))
        if self.error is not None:
            raise self.error
        return self.answer_text


def message(
    update_id: int,
    text: str | None = "hello",
    *,
    chat_id: int = OWNER,
    sender: int = OWNER,
    chat_type: str = "private",
    sent_at: datetime = NOW,
    kind: str = "message",
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "message_id": 1000 + update_id,
        "date": int(sent_at.timestamp()),
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": sender, "is_bot": False, "first_name": "Owner"},
    }
    if text is not None:
        body["text"] = text
    body.update(extra)
    return {"update_id": update_id, kind: body}


@dataclass
class Harness:
    time: FakeTime
    updates: FakeUpdates
    replies: FakeReplies
    tools: FakeTools
    desk: FakeDesk
    kill: FakeKillSwitch
    questions: FakeQuestions | None
    state: AssistantState
    assistant: TelegramAssistant
    fences: FakeFences

    async def feed(self, *updates: Mapping[str, Any]) -> None:
        self.updates.batches.append(list(updates))
        await self.assistant.poll_once()
        if self.questions is not None:
            await self.assistant.drain()


def make(tmp_path: Path, *, questions: FakeQuestions | None = None, with_questions: bool = True) -> Harness:
    time = FakeTime()
    updates = FakeUpdates()
    replies = FakeReplies()
    tools = FakeTools()
    desk = FakeDesk()
    kill = FakeKillSwitch()
    if questions is None and with_questions:
        questions = FakeQuestions()
    state = AssistantState.load(tmp_path / "telegram" / "state.json")
    fences = FakeFences()
    nonces = list(NONCES)
    assistant = TelegramAssistant(
        owner_id=OWNER,
        updates=updates,
        replies=replies,
        tools=tools,  # type: ignore[arg-type]
        desk=desk,
        kill_switch=kill,
        fences=fences,
        questions=questions,
        questions_unavailable="" if with_questions else "no Anthropic API key in the Keychain",
        state=state,
        clock=time.clock,
        monotonic=time.monotonic,
        sleep=time.sleep,
        new_nonce=lambda: nonces.pop(0),
    )
    return Harness(time, updates, replies, tools, desk, kill, questions, state, assistant, fences)


@pytest.fixture
async def harness(tmp_path: Path):
    h = make(tmp_path)
    await h.assistant.start()
    yield h
    await h.assistant.stop()


# --------------------------------------------------------------------------
# Owner pinning
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [
        message(1, chat_id=STRANGER, sender=STRANGER),
        message(2, chat_id=-100123, sender=OWNER, chat_type="group"),
        message(3, chat_id=-100123, sender=OWNER, chat_type="supergroup"),
        message(4, chat_id=OWNER, sender=OWNER, chat_type="channel"),
        message(5, chat_id=OWNER, sender=STRANGER),
        message(6, chat_id=STRANGER, sender=OWNER),
        message(7, sender_chat={"id": -100, "type": "channel"}),
        message(8, business_connection_id="biz"),
        message(9, kind="edited_message", chat_id=STRANGER, sender=STRANGER),
        {"update_id": 10, "callback_query": {"from": {"id": OWNER}}},
        {"update_id": 11, "my_chat_member": {"chat": {"id": OWNER, "type": "private"}}},
    ],
)
async def test_anything_but_the_owners_private_chat_is_dropped_silently(harness, update):
    await harness.feed(update)

    assert harness.replies.texts == []
    assert harness.desk.parsed == []
    assert harness.questions.asked == []
    assert harness.desk.recorded == [update["update_id"]]  # recorded, so never re-read
    assert harness.state.offset == update["update_id"] + 1


def test_a_bot_sender_is_never_the_owner():
    update = message(1)
    update["message"]["from"]["is_bot"] = True

    assert classify_update(update, OWNER).kind == "stranger"


@pytest.mark.parametrize(
    ("extra", "notice"),
    [
        ({"forward_origin": {"type": "user", "date": 1}}, bot.FORWARD_NOTICE),
        ({"forward_from": {"id": 1}}, bot.FORWARD_NOTICE),
        ({"forward_date": 1}, bot.FORWARD_NOTICE),
        ({"is_automatic_forward": True}, bot.FORWARD_NOTICE),
        ({"via_bot": {"id": 77, "is_bot": True}}, bot.VIA_BOT_NOTICE),
    ],
)
async def test_owner_forwards_and_via_bot_messages_are_refused_with_a_notice(harness, extra, notice):
    await harness.feed(message(1, "/buy 100 AAPL", **extra))

    assert harness.replies.texts == [notice]
    assert harness.desk.parsed == [] and harness.desk.submitted == []


async def test_owner_edits_are_ignored_with_the_exact_notice(harness):
    await harness.feed(message(1, "/buy 500 AAPL", kind="edited_message"))

    assert harness.replies.texts == [EDIT_NOTICE] == ["Edits are ignored — send a new message."]
    assert harness.desk.parsed == []


async def test_non_text_from_the_owner_gets_a_notice(harness):
    await harness.feed(message(1, None, photo=[{"file_id": "x"}], caption="/buy 100 AAPL"))

    assert harness.replies.texts == [bot.NOT_TEXT_NOTICE]
    assert harness.desk.parsed == []


# --------------------------------------------------------------------------
# Dedupe, offset, record-before-handle
# --------------------------------------------------------------------------


async def test_a_duplicate_update_is_handled_once(harness):
    await harness.feed(message(5, "/status"))
    harness.state.offset = None  # as if the offset file were lost
    await harness.feed(message(5, "/status"))

    assert harness.tools.calls == ["status"]
    assert harness.replies.texts == ["status text"]


async def test_offset_is_last_update_plus_one_and_survives_a_restart(tmp_path):
    h = make(tmp_path)
    await h.assistant.start()
    h.updates.batches = [[message(7, "/hot"), message(8, "/market")], []]
    await h.assistant.poll_once()
    await h.assistant.poll_once()
    await h.assistant.stop()

    assert h.updates.offsets == [None, 9]
    saved = json.loads((tmp_path / "telegram" / "state.json").read_text())
    assert saved["offset"] == 9
    assert stat.S_IMODE((tmp_path / "telegram" / "state.json").stat().st_mode) == 0o600

    again = make(tmp_path)
    await again.assistant.start()
    again.updates.batches = [[message(8, "/market"), message(9, "/hot")]]
    await again.assistant.poll_once()
    await again.assistant.stop()
    assert again.updates.offsets == [9]
    # Update 8 is below the offset: neither recorded nor handled again.
    assert again.desk.recorded == [9]
    assert again.tools.calls == ["hotlist"]


async def test_updates_are_handled_in_update_id_order(harness):
    await harness.feed(message(12, "/market"), message(11, "/hot"))

    assert harness.tools.calls == ["hotlist", "market"]


async def test_an_update_that_cannot_be_recorded_is_not_handled_or_confirmed(harness):
    harness.desk.record_error = OSError("database is locked")
    await harness.feed(message(20, "/buy 100 AAPL"))

    assert harness.replies.texts == []
    assert harness.desk.parsed == [] and harness.desk.submitted == []
    assert harness.state.offset is None
    assert harness.time.sleeps == [1.0]

    harness.desk.record_error = None
    await harness.feed(message(20, "/status"))
    assert harness.replies.texts == ["status text"]
    assert harness.state.offset == 21


# --------------------------------------------------------------------------
# Rate limit
# --------------------------------------------------------------------------


async def test_more_than_twenty_messages_a_minute_are_ignored_with_one_notice(harness):
    await harness.feed(*(message(n, "/hot") for n in range(1, 24)))

    assert harness.tools.calls == ["hotlist"] * 20
    assert harness.replies.texts.count(bot.FLOOD_NOTICE) == 1
    # Exits and the kill switch always go through.
    await harness.feed(message(30, "/stop"))
    assert harness.kill.stopped
    harness.time.mono += 61
    await harness.feed(message(31, "/hot"))
    assert harness.tools.calls[-1] == "hotlist" and len(harness.tools.calls) == 21


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "call"),
    [
        ("/status", "status"),
        ("/positions", "positions"),
        ("/pnl", "pnl"),
        ("/orders", "orders"),
        ("/hot", "hotlist"),
        ("/market", "market"),
        ("/signals", "signals"),
        ("/report", "report"),
        ("/STATUS@AutotraderBot", "status"),
    ],
)
async def test_read_commands_answer_from_the_data_tools(harness, text, call):
    await harness.feed(message(1, text))

    assert harness.tools.calls == [call]
    assert harness.replies.texts == [f"{call} text"]
    assert harness.questions.asked == [] and harness.desk.parsed == []


@pytest.mark.parametrize(
    ("text", "symbol"),
    [("/quote AAPL", "AAPL"), ("/quote $tsla", "$tsla"), ("/quote BF B", "BF B"), ("/quote nvda please", "nvda")],
)
async def test_quote_passes_the_symbol(harness, text, symbol):
    await harness.feed(message(1, text))

    assert harness.tools.quotes == [symbol]


async def test_quote_without_a_symbol_explains_usage(harness):
    await harness.feed(message(1, "/quote"))

    assert harness.tools.quotes == []
    assert harness.replies.texts[0].startswith("Usage: /quote SYM")


async def test_help_and_start_list_the_commands(harness):
    await harness.feed(message(1, "/help"), message(2, "/start"))

    assert harness.replies.texts == [bot.HELP_TEXT, bot.HELP_TEXT]
    for command in ("/status", "/pnl", "/positions", "/orders", "/hot", "/market", "/signals",
                    "/report", "/quote", "/buy", "/sell", "/close", "/closeall", "/cancel", "/stop"):
        assert command in bot.HELP_TEXT


async def test_stop_sets_the_kill_switch_and_says_there_is_no_resume(harness):
    await harness.feed(message(1, "/stop"))

    assert harness.kill.reasons == ["owner /stop via Telegram"]
    assert harness.replies.texts[0].startswith("🛑 Kill switch ON")
    assert "There is no /resume" in harness.replies.texts[0]


async def test_stop_that_does_not_take_is_reported_as_critical(harness):
    async def broken(*, reason: str) -> None:
        raise OSError("disk I/O error")

    harness.kill.stop = broken  # type: ignore[method-assign]
    await harness.feed(message(1, "/stop"))

    assert harness.replies.texts[0].startswith("🚨 Kill switch NOT confirmed (OSError)")
    assert "autotraderctl paper stop" in harness.replies.texts[0]


async def test_resume_explains_and_never_touches_the_kill_switch(harness):
    harness.kill.stopped = True
    await harness.feed(message(1, "/resume"))

    assert harness.kill.stopped and harness.kill.reasons == []
    assert harness.replies.texts == [bot.RESUME_TEXT]
    assert "no /resume" in bot.RESUME_TEXT and "stop-only" in bot.RESUME_TEXT


async def test_unknown_commands_point_to_help(harness):
    await harness.feed(message(1, "/frobnicate now"))

    assert harness.replies.texts == ["Unknown command /frobnicate — send /help for the list."]
    assert harness.desk.parsed == [] and harness.questions.asked == []


# --------------------------------------------------------------------------
# Orders: only through the desk, one per message, stale refused
# --------------------------------------------------------------------------

BUY = ParsedText("order", request={"action": "BUY", "symbol": "AAPL", "quantity": 100})


async def test_an_accepted_order_is_queued_and_every_status_change_is_reported(harness):
    harness.desk.parse_table["/buy 100 AAPL"] = BUY
    await harness.feed(message(1, "/buy 100 AAPL"))

    assert harness.replies.texts == ["🟢 Queued BUY 100 AAPL MKT · #a1b2c3 · /cancel a1b2c3"]
    assert len(harness.desk.submitted) == 1
    order_id = harness.desk.queued.order_id

    for status, fields, expected in [
        ("sending", {}, None),
        ("sent", {}, "📤 Sent to IBKR: #a1b2c3 BUY 100 AAPL MKT"),
        ("partially_filled", {"filled_qty": "40", "avg_fill_price": "227.10"},
         "💵 Partly filled #a1b2c3 BUY 100 AAPL MKT: 40 of 100 @ avg 227.10"),
        ("filled", {"filled_qty": "100", "avg_fill_price": "227.15"},
         "✅ Filled #a1b2c3 BUY 100 AAPL MKT @ avg 227.15"),
    ]:
        before = len(harness.replies.texts)
        harness.desk.rows[order_id] = view(status, **fields)
        await harness.assistant.check_orders()
        new = harness.replies.texts[before:]
        assert new == ([expected] if expected else [])

    # Terminal: no longer polled.
    harness.desk.row_queries.clear()
    await harness.assistant.check_orders()
    assert harness.desk.row_queries == []
    assert harness.state.notified[order_id] == "filled"


@pytest.mark.parametrize(
    ("status", "fields", "expected"),
    [
        ("rejected", {"error": "201 Order rejected: insufficient margin"},
         "❌ Rejected #a1b2c3 BUY 100 AAPL MKT — 201 Order rejected: insufficient margin"),
        ("error", {"error": "TWS disconnected before placeOrder"},
         "⚠️ Error on #a1b2c3 BUY 100 AAPL MKT — TWS disconnected before placeOrder"),
        ("expired", {}, "⌛ Expired #a1b2c3 BUY 100 AAPL MKT — the bridge did not send it in time; NOT sent"),
        ("cancelled", {"filled_qty": "0"}, "↩️ Cancelled #a1b2c3 BUY 100 AAPL MKT"),
    ],
)
async def test_terminal_failures_are_reported_with_their_reason(harness, status, fields, expected):
    harness.desk.parse_table["/buy 100 AAPL"] = BUY
    await harness.feed(message(1, "/buy 100 AAPL"))
    harness.desk.rows[harness.desk.queued.order_id] = view(status, **fields)

    await harness.assistant.check_orders()

    assert harness.replies.texts[-1] == expected


async def test_an_unknown_order_state_is_critical_and_stays_watched(harness, caplog):
    harness.desk.parse_table["/buy 100 AAPL"] = BUY
    await harness.feed(message(1, "/buy 100 AAPL"))
    order_id = harness.desk.queued.order_id
    harness.desk.rows[order_id] = view("unknown")

    with caplog.at_level(logging.CRITICAL, logger="autotrader.shadow.telegram"):
        await harness.assistant.check_orders()

    text = harness.replies.texts[-1]
    assert text.startswith("🚨 CRITICAL #a1b2c3") and "will NOT be resent" in text and "Check TWS" in text
    assert any("UNKNOWN" in record.message for record in caplog.records)
    harness.desk.rows[order_id] = view("filled", avg_fill_price="227.15")
    await harness.assistant.check_orders()
    assert harness.replies.texts[-1].startswith("✅ Filled #a1b2c3")


async def test_a_rejected_order_is_not_queued_and_says_why(harness):
    harness.desk.parse_table["/buy 100000 AAPL"] = BUY
    harness.desk.rejection = ("order_too_large", "BUY 100000 AAPL ~$22,715,000.00 is over 25% of net liquidation")

    await harness.feed(message(1, "/buy 100000 AAPL"))

    assert harness.replies.texts == [
        "❌ Not sent — BUY 100000 AAPL ~$22,715,000.00 is over 25% of net liquidation (order_too_large)"
    ]
    assert harness.desk.rows == {}
    assert harness.desk.outcomes[1] == "order:rejected:order_too_large"


async def test_an_order_older_than_sixty_seconds_is_never_sent(harness):
    harness.desk.parse_table["/sell 50 TSLA"] = BUY
    await harness.feed(message(1, "/sell 50 TSLA", sent_at=NOW - timedelta(seconds=61)))

    assert harness.replies.texts == ["⏱ Stale message (61 s) — NOT sent. Send it again if you still want it."]
    assert harness.desk.submitted == []


async def test_an_order_just_inside_sixty_seconds_is_submitted(harness):
    harness.desk.parse_table["/buy 100 AAPL"] = BUY
    await harness.feed(message(1, "/buy 100 AAPL", sent_at=NOW - timedelta(seconds=59)))

    assert len(harness.desk.submitted) == 1


async def test_old_questions_and_commands_are_still_answered(harness):
    await harness.feed(
        message(1, "/pnl", sent_at=NOW - timedelta(hours=3)),
        message(2, "how did we do today?", sent_at=NOW - timedelta(hours=3)),
    )

    assert harness.tools.calls == ["pnl"]
    assert [question for question, _ in harness.questions.asked] == ["how did we do today?"]


async def test_order_like_text_the_parser_refuses_never_reaches_claude(harness):
    harness.desk.parse_table["buy some apple maybe?"] = ParsedText(
        "refused", reason="Conditional orders aren't supported. Nothing sent.", code="conditional",
        syntax="Examples: /buy 100 AAPL",
    )
    await harness.feed(message(1, "buy some apple maybe?"))

    assert harness.replies.texts == [
        "❌ Not sent — Conditional orders aren't supported. Nothing sent. (conditional)\nExamples: /buy 100 AAPL"
    ]
    assert harness.desk.outcomes[1] == "order:refused:conditional"
    assert harness.questions.asked == [] and harness.desk.submitted == []


async def test_a_slash_order_command_the_parser_cannot_read_is_refused_not_asked(harness):
    await harness.feed(message(1, "/buy lots of AAPL"))

    assert harness.replies.texts[0].startswith("❌ Not sent — ")
    assert harness.questions.asked == [] and harness.desk.submitted == []


async def test_a_failure_while_submitting_says_nothing_was_sent(harness):
    harness.desk.parse_table["/buy 100 AAPL"] = BUY

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    harness.desk.submit = broken  # type: ignore[method-assign]
    await harness.feed(message(1, "/buy 100 AAPL"))

    assert "Nothing was sent to IBKR" in harness.replies.texts[0]


async def test_a_restart_reports_changes_missed_while_down(tmp_path):
    first = make(tmp_path)
    await first.assistant.start()
    first.desk.parse_table["/buy 100 AAPL"] = BUY
    await first.feed(message(1, "/buy 100 AAPL"))
    await first.assistant.stop()

    second = make(tmp_path)
    order_id = first.desk.queued.order_id
    second.desk.today = [view("filled", avg_fill_price="227.15")]
    second.desk.rows[order_id] = view("filled", avg_fill_price="227.15")
    already = view("cancelled", order_id="f" * 32, short_id="ffffff")
    second.state.notified[already.order_id] = "cancelled"
    second.desk.today.append(already)
    await second.assistant.start()
    await second.assistant.check_orders()
    await second.assistant.stop()

    assert second.replies.texts == ["✅ Filled #a1b2c3 BUY 100 AAPL MKT @ avg 227.15"]


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------


async def test_plain_language_goes_to_claude_and_the_answer_comes_back(harness):
    await harness.feed(message(1, "how are we doing today?"))

    assert harness.questions.asked == [("how are we doing today?", 1001)]
    assert harness.replies.texts == ["Here is the answer."]


async def test_an_answer_that_mentions_an_order_never_places_one(tmp_path):
    h = make(tmp_path, questions=FakeQuestions(answer="Send /buy 100 AAPL to buy it."))
    await h.assistant.start()
    await h.feed(message(1, "should I buy apple?"))
    await h.assistant.stop()

    assert h.replies.texts == ["Send /buy 100 AAPL to buy it."]
    assert h.desk.submitted == [] and h.desk.parsed == ["should I buy apple?"]


async def test_a_failing_question_gets_a_polite_answer(tmp_path):
    h = make(tmp_path, questions=FakeQuestions(error=RuntimeError("kaput")))
    await h.assistant.start()
    await h.feed(message(1, "what's up?"))
    await h.assistant.stop()

    assert h.replies.texts == ["Sorry — I couldn't answer that (RuntimeError). Commands still work: /help"]
    assert h.desk.submitted == []


async def test_questions_without_an_anthropic_key_are_refused_politely(tmp_path):
    h = make(tmp_path, with_questions=False)
    await h.assistant.start()
    await h.feed(message(1, "what's up?"))
    await h.assistant.stop()

    assert h.replies.texts == [
        "Questions are unavailable (no Anthropic API key in the Keychain) — commands still work: /help"
    ]


# --------------------------------------------------------------------------
# Polling failures
# --------------------------------------------------------------------------


async def test_poll_errors_back_off_and_a_conflict_is_critical(harness, caplog):
    harness.updates.batches = [
        TelegramAPIError("getUpdates failed: ConnectError"),
        TelegramAPIError("getUpdates failed: ConnectError"),
        TelegramAPIError("getUpdates failed: HTTP 409: Conflict", status=409, permanent=True),
        TelegramAPIError("rate limited", status=429, retry_after=7.0),
    ]
    with caplog.at_level(logging.WARNING, logger="autotrader.shadow.telegram"):
        for _ in range(4):
            await harness.assistant.poll_once()

    assert harness.time.sleeps == [1.0, 2.0, 30.0, 7.0]
    assert any(record.levelno == logging.CRITICAL and "409" in record.message for record in caplog.records)


async def test_a_revoked_token_stops_the_assistant_with_exit_78(tmp_path):
    h = make(tmp_path)
    h.updates.batches = [TelegramAPIError("getUpdates failed: HTTP 401", status=401, permanent=True)]

    assert await h.assistant.run() == bot.EXIT_CONFIG == 78


# --------------------------------------------------------------------------
# Claude responder
# --------------------------------------------------------------------------


@dataclass
class Usage:
    input_tokens: int = 1200
    output_tokens: int = 150
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class Response:
    content: list[Block]
    stop_reason: str = "end_turn"
    model: str = CHAT_MODEL
    usage: Usage = field(default_factory=Usage)
    _request_id: str = "req_test"


def text_reply(text: str) -> Response:
    return Response([Block("thinking"), Block("text", text=text)])


def tool_reply(name: str, call_id: str = "toolu_1", **arguments: Any) -> Response:
    return Response([Block("tool_use", id=call_id, name=name, input=arguments)], stop_reason="tool_use")


class FakeMessages:
    def __init__(self, script: Sequence[Response | BaseException | Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else text_reply("done")
        if callable(item) and not isinstance(item, Response):
            return await item()
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    def __init__(self, script: Sequence[Any]) -> None:
        self.messages = FakeMessages(script)


class FakeBudget:
    def __init__(self, allow: Sequence[bool] | None = None, error: BaseException | None = None) -> None:
        self.allow = list(allow) if allow is not None else None
        self.error = error
        self.reserved: list[tuple[date, Decimal]] = []
        self.settled: list[tuple[Any, Decimal]] = []

    async def reserve(self, day: date, estimate_usd: Decimal) -> Any | None:
        if self.error is not None:
            raise self.error
        self.reserved.append((day, estimate_usd))
        if self.allow is not None and not self.allow.pop(0):
            return None
        return f"r{len(self.reserved)}"

    async def settle(self, reservation: Any, actual_usd: Decimal) -> None:
        self.settled.append((reservation, actual_usd))


class FakeToolRunner:
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error = error

    async def run_tool(self, name: str, arguments: Mapping[str, Any]) -> str:
        self.calls.append((name, dict(arguments)))
        if self.error is not None:
            raise self.error
        return f"{name}: data as of 10:29:58 ET"


def responder(tmp_path: Path, client: FakeClient, *, budget: FakeBudget | None = None,
              tools: FakeToolRunner | None = None, time: FakeTime | None = None,
              deadline: float = bot.CHAT_DEADLINE_SECONDS) -> tuple[ChatResponder, FakeBudget, FakeToolRunner, Path]:
    budget = budget or FakeBudget()
    tools = tools or FakeToolRunner()
    clock = time or FakeTime()
    directory = tmp_path / "telegram"
    chat = ChatResponder(
        client=client,
        tools=tools,
        budget=budget,
        memory=ChatMemory(directory / "chat"),
        audit=AuditLog(directory / "audit"),
        clock=clock.clock,
        deadline_seconds=deadline,
    )
    return chat, budget, tools, directory


def audit_records(directory: Path) -> list[dict[str, Any]]:
    path = directory / "audit" / "2026-10-13.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_a_question_reserves_then_settles_the_actual_cost_and_is_audited(tmp_path):
    client = FakeClient([text_reply("We are flat, no trades yet.")])
    chat, budget, _tools, directory = responder(tmp_path, client)

    answer = await chat.answer("how are we doing?", message_id=77)

    assert answer == "We are flat, no trades yet."
    (call,) = client.messages.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "low"}
    assert call["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert call["tools"] == list(TOOL_DEFINITIONS)
    assert "thinking" not in call  # Opus 5.5 thinking cannot be disabled; effort controls it
    assert call["cache_control"] == {"type": "ephemeral"}
    assert call["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}
    assert call["system"] == bot.SYSTEM_PROMPT
    prompt = call["messages"][-1]["content"]
    assert prompt.startswith("[Tue 13 Oct 2026 10:30:00 ET · market OPEN") and prompt.endswith("how are we doing?")
    ((day, estimate),) = budget.reserved
    assert day == date(2026, 10, 13)
    # Worst case: 4096 output tokens at $50/M alone is $0.2048.
    assert estimate > Decimal("0.2048")
    # 1200 in at $4/M + 150 out at $20/M.
    assert budget.settled == [("r1", Decimal("0.0078"))]
    records = audit_records(directory)
    assert [record["event"] for record in records] == ["claude_call", "question"]
    assert records[0]["request_id"] == "req_test" and records[0]["cost_usd"] == "0.0078"
    assert records[1]["outcome"] == "answered" and records[1]["message_id"] == 77
    assert stat.S_IMODE((directory / "audit" / "2026-10-13.jsonl").stat().st_mode) == 0o600


async def test_tools_are_called_one_at_a_time_and_their_results_go_back(tmp_path):
    client = FakeClient([
        tool_reply("get_positions"),
        tool_reply("get_quote", call_id="toolu_2", symbol="AAPL"),
        text_reply("You hold 100 AAPL, last 227.15."),
    ])
    chat, budget, tools, directory = responder(tmp_path, client)

    answer = await chat.answer("what do I hold and what's apple at?", message_id=1)

    assert answer == "You hold 100 AAPL, last 227.15."
    assert tools.calls == [("get_positions", {}), ("get_quote", {"symbol": "AAPL"})]
    third = client.messages.calls[2]["messages"]
    assert third[-1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "get_quote: data as of 10:29:58 ET"}],
    }
    assert third[-2]["role"] == "assistant"  # the full response content, thinking included
    assert len(budget.reserved) == 3 and len(budget.settled) == 3
    summary = audit_records(directory)[-1]
    assert [tool["name"] for tool in summary["tools"]] == ["get_positions", "get_quote"]


async def test_tool_rounds_are_capped_at_six_then_claude_must_answer(tmp_path):
    client = FakeClient([tool_reply("get_status", call_id=f"t{n}") for n in range(6)] + [text_reply("Best I can say.")])
    chat, _budget, tools, _directory = responder(tmp_path, client)

    answer = await chat.answer("dig deep", message_id=1)

    assert answer == "Best I can say."
    assert len(tools.calls) == bot.MAX_TOOL_ROUNDS == 6
    assert [call["tool_choice"]["type"] for call in client.messages.calls] == ["auto"] * 6 + ["none"]


async def test_a_tool_call_after_the_cap_is_never_executed(tmp_path):
    client = FakeClient([tool_reply("get_status", call_id=f"t{n}") for n in range(8)])
    chat, _budget, tools, _directory = responder(tmp_path, client)

    answer = await chat.answer("dig deeper", message_id=1)

    assert len(tools.calls) == 6
    assert "more lookups" in answer


async def test_over_budget_replies_and_never_calls_claude(tmp_path):
    client = FakeClient([text_reply("unused")])
    chat, budget, _tools, directory = responder(tmp_path, client, budget=FakeBudget(allow=[False]))

    assert await chat.answer("hi", message_id=1) == BUDGET_NOTICE == "Daily question budget reached — commands still work."
    assert client.messages.calls == []
    assert audit_records(directory)[-1]["outcome"] == "budget"


async def test_budget_running_out_mid_question_stops_the_loop(tmp_path):
    client = FakeClient([tool_reply("get_pnl"), text_reply("unused")])
    chat, budget, tools, _directory = responder(tmp_path, client, budget=FakeBudget(allow=[True, False]))

    assert await chat.answer("pnl?", message_id=1) == BUDGET_NOTICE
    assert len(client.messages.calls) == 1 and len(budget.settled) == 1


async def test_a_budget_ledger_failure_fails_closed(tmp_path):
    client = FakeClient([text_reply("unused")])
    chat, _budget, _tools, _directory = responder(tmp_path, client, budget=FakeBudget(error=OSError("locked")))

    answer = await chat.answer("hi", message_id=1)

    assert "budget ledger failed" in answer and client.messages.calls == []


class ConnectionDropped(Exception):
    """Like anthropic.APIConnectionError: no HTTP status came back."""


class BadRequest(Exception):
    status_code = 400


async def test_a_claude_failure_is_polite_and_charges_only_what_may_have_been_billed(tmp_path):
    client = FakeClient([ConnectionDropped("reset"), BadRequest("invalid")])
    chat, budget, _tools, directory = responder(tmp_path, client)

    first = await chat.answer("hi", message_id=1)
    second = await chat.answer("hi again", message_id=2)

    assert first == "Sorry — I couldn't get an answer from Claude (ConnectionDropped). Commands still work: /help"
    assert second.startswith("Sorry — I couldn't get an answer from Claude (BadRequest)")
    # No HTTP status: maybe billed, so the estimate stays charged. A 400: not billed.
    assert budget.settled == [("r1", budget.reserved[0][1]), ("r2", Decimal("0"))]
    errors = [record for record in audit_records(directory) if record["event"] == "claude_call"]
    assert [record["error"] for record in errors] == ["ConnectionDropped", "BadRequest"]


async def test_the_overall_deadline_stops_a_slow_answer(tmp_path):
    async def slow() -> Response:
        await asyncio.sleep(10)
        return text_reply("too late")

    client = FakeClient([slow])
    chat, budget, _tools, directory = responder(tmp_path, client, deadline=1.3)

    answer = await chat.answer("hi", message_id=1)

    assert answer.startswith("Sorry — that took longer than 1 s")
    # Unknown billing: the reservation stays charged at the estimate.
    assert budget.settled == [("r1", budget.reserved[0][1])]
    assert audit_records(directory)[-1]["outcome"] == "deadline"


async def test_tool_failures_are_reported_to_claude_as_errors(tmp_path):
    client = FakeClient([tool_reply("get_positions"), text_reply("IBKR is unreachable right now.")])
    chat, _budget, _tools, _directory = responder(
        tmp_path, client, tools=FakeToolRunner(error=ToolError("TWS is not reachable"))
    )

    assert await chat.answer("positions?", message_id=1) == "IBKR is unreachable right now."
    result = client.messages.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and result["content"] == "TWS is not reachable"


async def test_a_refusal_is_a_polite_answer(tmp_path):
    client = FakeClient([Response([], stop_reason="refusal")])
    chat, _budget, _tools, _directory = responder(tmp_path, client)

    assert (await chat.answer("hmm", message_id=1)).startswith("Sorry — I can't help with that one.")


async def test_a_fallback_model_is_charged_at_the_ceiling_price(tmp_path):
    client = FakeClient([replace(text_reply("ok"), model="claude-opus-5")])
    chat, budget, _tools, _directory = responder(tmp_path, client)

    await chat.answer("hi", message_id=1)

    # 1200 in at $10/M + 150 out at $50/M.
    assert budget.settled == [("r1", Decimal("0.0195"))]


async def test_todays_conversation_is_remembered_persisted_and_capped(tmp_path):
    client = FakeClient([text_reply(f"answer {n}") for n in range(25)])
    time = FakeTime()
    chat, _budget, _tools, directory = responder(tmp_path, client, time=time)

    await chat.answer("first question", message_id=1)
    await chat.answer("second question", message_id=2)

    second = client.messages.calls[1]["messages"]
    assert [item["role"] for item in second] == ["user", "assistant", "user"]
    assert second[0]["content"].endswith("first question") and second[1]["content"] == "answer 0"
    stored = json.loads((directory / "chat" / "2026-10-13.json").read_text())
    assert [turn["assistant"] for turn in stored["turns"]] == ["answer 0", "answer 1"]
    assert stat.S_IMODE((directory / "chat" / "2026-10-13.json").stat().st_mode) == 0o600

    # A restart reloads today's memory.
    reloaded = ChatMemory(directory / "chat")
    assert [turn.assistant for turn in reloaded.turns(date(2026, 10, 13))] == ["answer 0", "answer 1"]

    for n in range(3, 25):
        await chat.answer(f"question {n}", message_id=n)
    last = client.messages.calls[-1]["messages"]
    assert len(last) == 2 * bot.HISTORY_TURNS + 1

    # A new day starts empty.
    time.now += timedelta(days=1)
    await chat.answer("new day", message_id=99)
    assert len(client.messages.calls[-1]["messages"]) == 1


async def test_failed_questions_are_not_remembered(tmp_path):
    client = FakeClient([ConnectionDropped("x"), text_reply("fine")])
    chat, _budget, _tools, _directory = responder(tmp_path, client)

    await chat.answer("lost question", message_id=1)
    await chat.answer("next", message_id=2)

    assert len(client.messages.calls[1]["messages"]) == 1


def test_claude_gets_data_tools_only_with_strict_schemas():
    names = {tool["name"] for tool in TOOL_DEFINITIONS}

    assert names == {
        "get_status", "get_positions", "get_pnl", "get_orders", "get_hotlist",
        "get_market_context", "get_signals", "get_quote", "get_report",
    }
    for tool in TOOL_DEFINITIONS:
        assert tool["strict"] is True
        schema = tool["input_schema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
    forbidden = ("order", "buy", "sell", "cancel", "close", "place", "trade")
    assert not any(word in name for name in names - {"get_orders"} for word in forbidden)
    assert "cannot place, change, or cancel orders" in bot.SYSTEM_PROMPT
    assert "Never invent" in bot.SYSTEM_PROMPT


# --------------------------------------------------------------------------
# Startup refusals and whoami
# --------------------------------------------------------------------------


async def closed_gateway() -> bool:
    """The composition root's Gateway probe in these tests: never the real port
    (tests/conftest.py fails any test that reaches 7497)."""
    return False


def env(tmp_path: Path, **overrides: Any) -> AssistantEnvironment:
    base: dict[str, Any] = {
        "paths": UserPaths(home=tmp_path),
        "read_token": lambda: TOKEN,
        "read_anthropic_key": lambda: "sk-ant-test-key-0123456789",
        "out": io.StringIO(),
        "configure_logging": False,
        "gateway_probe": closed_gateway,
    }
    base.update(overrides)
    return AssistantEnvironment(**base)


async def test_no_pinned_owner_refuses_with_78_and_never_discovers(tmp_path):
    def no_network():
        raise AssertionError("the token must not even be read")

    environment = env(tmp_path, read_token=no_network)

    assert await bot.run_telegram_assistant(env=environment) == 78
    assert "refusing to start" in environment.out.getvalue()
    assert not telegram_config_file(environment.paths).exists()


async def test_an_invalid_pin_refuses_with_78(tmp_path):
    environment = env(tmp_path)
    config = telegram_config_file(environment.paths)
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"chat_id": "not-a-number"}))

    assert await bot.run_telegram_assistant(env=environment) == 78
    assert "unusable" in environment.out.getvalue()


async def test_a_missing_token_refuses_with_78(tmp_path):
    from trading.operations.keychain import KeychainReadError

    def missing() -> str:
        raise KeychainReadError("unable to read")

    environment = env(tmp_path, read_token=missing)
    save_chat_id(telegram_config_file(environment.paths), OWNER)

    assert await bot.run_telegram_assistant(env=environment) == 78
    assert "no usable bot token" in environment.out.getvalue()


async def test_a_second_consumer_exits_75(tmp_path):
    from trading.operations.supervisor import single_instance

    environment = env(tmp_path)
    save_chat_id(telegram_config_file(environment.paths), OWNER)
    with single_instance(environment.paths.data_directory / "telegram.lock", label="Telegram assistant"):
        assert await bot.run_telegram_assistant(env=environment) == 75
    assert "another Telegram assistant is already running" in environment.out.getvalue()


async def test_a_missing_ledger_refuses_with_78(tmp_path, monkeypatch):
    environment = env(tmp_path)
    save_chat_id(telegram_config_file(environment.paths), OWNER)
    monkeypatch.setattr(bot, "MANUAL_ORDERS_MODULE", "trading.execution.no_such_ledger")

    assert await bot.run_telegram_assistant(env=environment) == 78


def test_whoami_prints_the_pinned_owner_and_no_secret(tmp_path):
    paths = UserPaths(home=tmp_path)
    out = io.StringIO()
    assert bot.print_owner(paths=paths, out=out) == 1
    assert "No owner pinned" in out.getvalue() and "notify test" in out.getvalue()

    save_chat_id(telegram_config_file(paths), OWNER)
    out = io.StringIO()
    assert bot.print_owner(paths=paths, out=out) == 0
    assert f"Owner chat id: {OWNER}" in out.getvalue()
    assert "token" not in out.getvalue().lower()


def test_cli_telegram_commands(monkeypatch, capsys):
    from trading import cli

    class Stub:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def print_owner(self) -> int:
            self.calls.append("whoami")
            return 0

        async def run_telegram_assistant(self) -> int:
            self.calls.append("run")
            raise RuntimeError(TOKEN)

    stub = Stub()
    monkeypatch.setitem(sys.modules, cli.TELEGRAM_BOT_MODULE, stub)

    def schema_check(**_kwargs: object) -> SimpleNamespace:
        stub.calls.append("schema_check")
        return SimpleNamespace(current=True)

    monkeypatch.setattr(cli, "require_current_schema", schema_check)

    assert cli.main(["telegram", "whoami"]) == 0
    assert cli.main(["telegram", "run"]) == 1
    # The schema is checked (never migrated) before the assistant opens the
    # manual-order ledger.
    assert stub.calls == ["whoami", "schema_check", "run"]
    error = capsys.readouterr().err
    assert "RuntimeError" in error and TOKEN not in error
    with pytest.raises(SystemExit) as refused:
        cli.main(["--db-path", "/tmp/x.db", "telegram", "run"])
    assert refused.value.code == 2


# --------------------------------------------------------------------------
# Routing rules shared with the ledger
# --------------------------------------------------------------------------


async def test_bare_stop_is_the_kill_switch_but_stop_with_arguments_is_an_order(harness):
    stop_order = ParsedText("order", request={"action": "SELL_STOP"})
    harness.desk.parse_table["/stop TSLA 240"] = stop_order

    await harness.feed(message(1, "/stop TSLA 240"))
    assert not harness.kill.stopped
    assert [parsed for parsed, _ in harness.desk.submitted] == [stop_order]

    await harness.feed(message(2, "/stop@AutotraderBot"))
    assert harness.kill.stopped
    assert harness.desk.outcomes[2] == "kill_switch:on"


async def test_exits_pass_the_flood_limit_even_as_sentences(harness):
    harness.desk.exits.add("sell 50 TSLA")
    harness.desk.parse_table["sell 50 TSLA"] = ParsedText("order", request={"action": "SELL"})
    await harness.feed(*(message(n, "/hot") for n in range(1, 22)))

    await harness.feed(message(30, "sell 50 TSLA"), message(31, "/buy 1 AAPL"))

    assert len(harness.desk.submitted) == 1
    assert harness.desk.outcomes[31] == "rate_limited"


async def test_a_stale_cancel_is_still_submitted(harness):
    cancel = ParsedText("order", request={"action": "CANCEL"}, is_cancel=True)
    harness.desk.parse_table["/cancel a1b2c3"] = cancel

    await harness.feed(message(1, "/cancel a1b2c3", sent_at=NOW - timedelta(minutes=5)))

    assert [parsed for parsed, _ in harness.desk.submitted] == [cancel]


async def test_every_update_gets_an_outcome_and_the_ledger_sees_chat_and_type(harness):
    await harness.feed(
        message(1, chat_id=STRANGER, sender=STRANGER),
        message(2, "/status"),
        message(3, "/buy 1 AAPL", kind="edited_message"),
        message(4, "what moved today?"),
    )

    assert harness.desk.outcomes == {
        1: "dropped:stranger",
        2: "command:status",
        3: "refused:edit",
        4: "question:queued",
    }
    first, _, third, _ = harness.desk.inbounds
    assert (first.chat_id, first.update_type) == (STRANGER, "message")
    assert (third.chat_id, third.update_type) == (OWNER, "edited_message")


async def test_rows_a_cancel_reply_already_reported_are_not_reported_again(harness):
    harness.desk.parse_table["/buy 100 AAPL"] = BUY
    await harness.feed(message(1, "/buy 100 AAPL"))
    cancelled = view("cancelled")
    harness.desk.rows[ORDER_ID] = cancelled

    async def cancel_submit(parsed, inbound, *, chat_id, now):
        return Submission(True, "↩️ Cancel #a1b2c3:\n• #a1b2c3 BUY 100 AAPL MKT: cancelled before it was sent",
                          order_id="c" * 32, watch=(cancelled,))

    harness.desk.submit = cancel_submit  # type: ignore[method-assign]
    harness.desk.parse_table["/cancel a1b2c3"] = ParsedText("order", request={}, is_cancel=True)
    await harness.feed(message(2, "/cancel a1b2c3"))
    before = list(harness.replies.texts)
    await harness.assistant.check_orders()

    assert harness.replies.texts == before
    assert before[-1].startswith("↩️ Cancel #a1b2c3:")


# --------------------------------------------------------------------------
# The real ledger (trading.execution.manual_orders) behind the desk
# --------------------------------------------------------------------------

ACCOUNT = "DU1234567"
TRADE_NOW = datetime(2026, 10, 13, 10, 30, tzinfo=ET).astimezone(UTC)


class DeskActor:
    """TWS data client 21 as the desk sees it."""

    def __init__(self, *, positions: Sequence[tuple[str, int, int]] = (), accounts=(ACCOUNT,),
                 bid: str = "227.14", ask: str = "227.16", connect_error: Exception | None = None,
                 quote_error: Exception | None = None) -> None:
        self.connected = False
        self.managed_accounts = frozenset(accounts)
        self.positions = positions
        self.bid, self.ask = Decimal(bid), Decimal(ask)
        self.connect_error = connect_error
        self.quote_error = quote_error
        self.connects = 0

    async def connect(self) -> None:
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def account_snapshot(self):
        from trading.contracts import AccountSnapshot, RawAccountPosition

        return AccountSnapshot(
            paper_account_id=ACCOUNT, client_id=21, observed_at=TRADE_NOW,
            net_liquidation=Decimal("1000000"), available_funds=Decimal("900000"),
            realized_day_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
            positions=tuple(
                RawAccountPosition(
                    paper_account_id=ACCOUNT, con_id=con_id, symbol=symbol, quantity=Decimal(qty),
                    market_price=Decimal("10"), market_value=Decimal(qty) * 10, average_cost=Decimal("9"),
                    unrealized_pnl=Decimal("0"), realized_pnl=Decimal("0"), observed_at=TRADE_NOW,
                )
                for symbol, con_id, qty in self.positions
            ),
        )

    async def market_snapshot(self, con_id: int, *, timeout_seconds: float = 3.0):
        from trading.market.snapshots import MarketSnapshot

        if self.quote_error is not None:
            raise self.quote_error
        return MarketSnapshot(
            con_id=con_id, observed_at=TRADE_NOW - timedelta(seconds=1), prior_close=Decimal("224.37"),
            last=(self.bid + self.ask) / 2, bid=self.bid, ask=self.ask,
        )

    async def contract_details_for_symbol(self, symbol: str, primary_exchange: str | None):
        from trading.market.ibkr_boundary import IBKRContractDetails

        con_ids = {"AAPL": 265598, "TSLA": 76792991, "BF B": 4815747}
        if symbol not in con_ids:
            raise RuntimeError(f"no contract for {symbol}")
        return IBKRContractDetails(
            con_id=con_ids[symbol], symbol=symbol, currency="USD", security_type="STK",
            stock_type="COMMON", primary_exchange="NASDAQ", min_tick=Decimal("0.01"),
        )


@dataclass
class DeskWorld:
    desk: Any
    actor: DeskActor
    kill: FakeKillSwitch
    paths: UserPaths
    ledger: Any
    next_message: int = 1

    def inbound(self, text: str, *, age: float = 2.0) -> Inbound:
        number = self.next_message
        self.next_message += 1
        return Inbound(
            "text", 500 + number, 900 + number, TRADE_NOW - timedelta(seconds=age), text=text,
            chat_id=OWNER, update_type="message",
        )

    async def send(self, text: str, *, age: float = 2.0, now: datetime = TRADE_NOW) -> Submission:
        inbound = self.inbound(text, age=age)
        parsed = self.desk.parse(text)
        assert parsed.kind == "order", parsed
        return await self.desk.submit(parsed, inbound, chat_id=OWNER, now=now)


async def desk_world(tmp_path: Path, monkeypatch, actor: DeskActor | None = None, *,
                     heartbeat: bool = True) -> DeskWorld:
    from config import cfg
    from db.core import init_db
    from trading.execution import manual_orders as ledger
    from trading.operations.telegram_tools import DataTools, TWSDataSession

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "desk.db"))
    await init_db()
    if heartbeat:
        await ledger.write_bridge_heartbeat("loop", now=TRADE_NOW - timedelta(seconds=3), pid=4242)
    actor = actor or DeskActor()
    kill = FakeKillSwitch()
    paths = UserPaths(home=tmp_path / "home")
    tools = DataTools(
        paths=paths,
        tws=TWSDataSession(account=ACCOUNT, factory=lambda config: actor),
        clock=lambda: TRADE_NOW,
        kill_switch=kill,
    )
    desk = bot.ManualOrdersDesk(tools=tools, kill_switch=kill)
    await desk.check_ready()
    return DeskWorld(desk, actor, kill, paths, ledger)


def bridge_state(paths: UserPaths, entries: dict[str, dict[str, Any]]) -> None:
    path = paths.data_directory / "bridge" / "2026-10-13.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_date": "2026-10-13", "submitted": entries}))


async def test_the_desk_parses_with_the_ledgers_own_parser(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)
    desk = world.desk

    assert desk.parse("/buy 100 AAPL").kind == "order"
    assert desk.parse("buy 100 shares of $AAPL at 180").kind == "order"
    assert desk.parse("/cancel a3f9c2").is_cancel
    refused = desk.parse("sell everything")
    assert (refused.kind, refused.code) == ("refused", "closeall_exact")
    assert refused.syntax.startswith("Examples: /buy 100 AAPL")
    assert desk.parse("buy 100 ALL").code == "word_ticker_needs_cashtag"
    assert desk.parse("how are we doing today?").kind == "not_order"
    assert desk.is_kill_switch("/stop") and not desk.is_kill_switch("/stop TSLA 240")
    assert desk.is_exit("sell 50 TSLA") and desk.is_exit("/close NVDA") and desk.is_exit("/stop")
    assert not desk.is_exit("/buy 100 AAPL")


async def test_updates_are_recorded_once_with_their_outcome(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)
    inbound = Inbound("stranger", 77, 12, chat_id=STRANGER, update_type="message")

    assert await world.desk.record_update(inbound, received_at=TRADE_NOW) is True
    assert await world.desk.record_update(inbound, received_at=TRADE_NOW) is False
    await world.desk.mark_processed(77, "dropped:stranger", at=TRADE_NOW)
    assert await world.ledger.max_update_id() == 77


async def test_a_valid_buy_is_queued_with_the_exact_reply_and_reported_as_it_fills(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)

    submission = await world.send("/buy 100 AAPL")

    assert submission.accepted, submission.reply
    short = submission.order_id[:6]
    assert submission.reply == f"🟢 Queued BUY 100 AAPL MKT · #{short} · /cancel {short}"
    row = await world.ledger.get_order(submission.order_id)
    assert (row.status, row.action, row.quantity, row.con_id, row.source) == ("queued", "BUY", 100, 265598, "command")
    assert row.audit["caller"]["facts"]["accounts_all_paper"] is True
    assert row.audit["caller"]["contract"]["con_id"] == 265598

    claimed = await world.ledger.claim_next_order(TRADE_NOW + timedelta(seconds=1))
    assert claimed.id == submission.order_id
    await world.ledger.update_order_status(claimed.id, "sent", now=TRADE_NOW, ibkr_order_id=11)
    await world.ledger.update_order_status(
        claimed.id, "filled", now=TRADE_NOW, filled_qty=100, avg_fill_price=Decimal("227.15")
    )
    (current,) = await world.desk.orders([submission.order_id])
    assert bot.status_change_text(current) == f"✅ Filled #{short} BUY 100 AAPL MKT @ avg 227.15"


async def test_after_hours_the_desk_says_market_closed_without_touching_tws(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)
    tonight = datetime(2026, 10, 12, 23, 30, tzinfo=ET).astimezone(UTC)

    sent_two_seconds_ago = (TRADE_NOW - tonight).total_seconds() + 2
    submission = await world.send("/buy 100 AAPL", now=tonight, age=sent_two_seconds_ago)

    assert submission.reply == (
        "❌ Not sent — Market closed: manual orders run 09:30-15:54 ET on trading days. Nothing sent. (market_closed)"
    )
    assert world.actor.connects == 0
    assert await world.ledger.list_active_orders() == ()


@pytest.mark.parametrize(
    ("setup", "text", "code"),
    [
        ("no_heartbeat", "/buy 100 AAPL", "bridge_offline"),
        ("live_account", "/buy 100 AAPL", "not_paper"),
        ("tws_down", "/buy 100 AAPL", "account_unavailable"),
        ("kill_switch", "/buy 100 AAPL", "kill_switch"),
        ("no_quote", "/buy 100 AAPL", "no_fresh_quote"),
        ("wide_spread", "/buy 100 AAPL", "spread_too_wide"),
        ("none", "/sell 50 TSLA", "insufficient_shares"),
        ("none", "/buy 100 ZZZZ", "unknown_symbol"),
        ("none", "/buy 2000 AAPL", "order_too_large"),
        ("none", "/buy 100 AAPL 300", "price_out_of_band"),
    ],
)
async def test_the_validator_refuses_with_named_codes(tmp_path, monkeypatch, setup, text, code):
    from trading.market.tws_actor import TWSConnectionError, TWSRequestError

    actor = DeskActor(
        accounts=(ACCOUNT, "U1234567") if setup == "live_account" else (ACCOUNT,),
        connect_error=TWSConnectionError("refused") if setup == "tws_down" else None,
        quote_error=TWSRequestError("delayed data rejected") if setup == "no_quote" else None,
        bid="200.00" if setup == "wide_spread" else "227.14",
    )
    world = await desk_world(tmp_path, monkeypatch, actor, heartbeat=setup != "no_heartbeat")
    world.kill.stopped = setup == "kill_switch"

    submission = await world.send(text)

    assert not submission.accepted
    assert submission.code == code, submission.reply
    assert submission.reply.startswith("❌ Not sent — ") and submission.reply.endswith(f"({code})")
    assert await world.ledger.list_active_orders() == ()


async def test_a_too_old_message_is_refused_by_the_ledger_too(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)

    submission = await world.send("/buy 100 AAPL", age=75)

    assert submission.code == "stale_message"


async def test_sells_respect_the_owners_shares_and_the_kill_switch_never_blocks_them(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch, DeskActor(positions=[("TSLA", 76792991, 80)], bid="249.90", ask="250.10"))
    world.kill.stopped = True

    half = await world.send("/sell half TSLA")
    assert half.accepted, half.reply
    assert half.reply.startswith("🟢 Queued SELL 40 TSLA MKT · #")
    # 40 of the 80 are now reserved by the working sell: 50 more would be a short.
    over = await world.send("/sell 50 TSLA")
    assert over.code == "insufficient_shares"
    stop = await world.send("/stop TSLA 240")
    assert stop.accepted, stop.reply
    assert " SELL 40 TSLA STP 240.00 · #" in stop.reply


async def test_bot_held_symbols_only_allow_close_which_flattens_the_bracket(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch, DeskActor(positions=[("AAPL", 265598, 100)]))
    bridge_state(world.paths, {"d1": {"symbol": "AAPL", "status": "open", "qty": 100, "filled": 100}})

    buy = await world.send("/buy 10 AAPL")
    assert buy.code == "bot_holds_symbol"
    close = await world.send("/close AAPL")
    assert close.accepted, close.reply
    short = close.order_id[:6]
    assert close.reply == f"🟢 Queued CLOSE AAPL bot bracket · #{short} · /cancel {short}"


async def test_l4_a_bot_close_the_bridge_started_is_never_reported_as_being_cancelled(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch, DeskActor(positions=[("AAPL", 265598, 100)]))
    bridge_state(world.paths, {"d1": {"symbol": "AAPL", "status": "open", "qty": 100, "filled": 100}})
    close = await world.send("/close AAPL")
    claimed = await world.ledger.claim_next_order(TRADE_NOW)  # the bridge takes it and starts closing
    assert claimed is not None and claimed.id == close.order_id
    assert await world.ledger.update_order_status(close.order_id, "sent", now=TRADE_NOW)
    short = close.order_id[:6]

    cancel = await world.send(f"/cancel {short}")

    # The bridge refuses to cancel a bot close it started; the reply must say so, not "cancelling".
    assert cancel.reply == (
        f"↩️ Cancel #{short}:\n• #{short} CLOSE AAPL bot bracket: closing a bot bracket cannot be cancelled;"
        " the bridge keeps closing it"
    )


@pytest.mark.parametrize(("status", "head"), [
    ("sent", "📤 Sent to IBKR: #abc123 CLOSE ALL"),
    ("partially_filled", "💵 Partly filled #abc123 CLOSE ALL: 50 of ?"),
    ("filled", "✅ Filled #abc123 CLOSE ALL 50 sh"),
    ("cancelled", "↩️ Cancelled #abc123 CLOSE ALL (50 filled before)"),
])
def test_r3_a_closeall_reply_always_shows_what_was_not_sold(status: str, head: str) -> None:
    from trading.operations.telegram_tools import order_view

    row = {"id": "abc123" + "0" * 26, "action": "CLOSE_ALL", "status": status, "filled_qty": 50,
           "error": "AAPL: spread 2.13% over 1% — not sold"}

    assert bot.status_change_text(order_view(row)) == f"{head} — AAPL: spread 2.13% over 1% — not sold"


async def test_l1_an_unreadable_kill_switch_blocks_buys_and_never_sells(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch, DeskActor(positions=[("TSLA", 76792991, 80)], bid="249.90",
                                                              ask="250.10"))

    async def unreadable() -> bool:
        raise OSError("fence unreadable")

    world.kill.is_stopped = unreadable  # type: ignore[method-assign]

    buy = await world.send("/buy 100 AAPL")
    assert buy.code == "kill_switch", buy.reply
    sell = await world.send("/sell 10 TSLA")
    assert sell.accepted, sell.reply


async def test_l1_an_unreadable_bridge_heartbeat_counts_as_offline(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)

    async def unreadable(now: datetime) -> float:
        raise OSError("heartbeat unreadable")

    monkeypatch.setattr(world.ledger, "bridge_heartbeat_age", unreadable)

    submission = await world.send("/buy 100 AAPL")

    assert submission.code == "bridge_offline", submission.reply


async def test_an_unreadable_bridge_state_fails_closed(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)
    path = world.paths.data_directory / "bridge" / "2026-10-13.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    submission = await world.send("/buy 100 AAPL")

    assert submission.code == "bot_state_unavailable"


async def test_closeall_and_cancels(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)
    buy = await world.send("/buy 100 AAPL")
    short = buy.order_id[:6]

    cancel = await world.send(f"/cancel {short}")
    assert cancel.accepted
    assert cancel.reply == f"↩️ Cancel #{short}:\n• #{short} BUY 100 AAPL MKT: cancelled before it was sent"
    assert [view.status for view in cancel.watch] == ["cancelled"]

    again = await world.send(f"/cancel {short}")
    assert not again.accepted
    assert again.reply == f"❌ Not cancelled — #{short}: order is cancelled (no_active_order)"

    closeall = await world.send("/closeall")
    assert closeall.accepted, closeall.reply
    assert closeall.reply.startswith("🟢 Queued CLOSE ALL · #")


async def test_a_cancel_works_from_a_recent_handshake_while_tws_is_away(tmp_path, monkeypatch):
    from trading.market.tws_actor import TWSConnectionError

    world = await desk_world(tmp_path, monkeypatch)
    buy = await world.send("/buy 100 AAPL")
    await world.desk.tools.tws.close()
    world.actor.connect_error = TWSConnectionError("TWS restarting")

    cancel = await world.send(f"/cancel {buy.order_id[:6]}", age=600)

    assert cancel.accepted, cancel.reply


async def test_todays_orders_skip_cancel_rows(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)
    buy = await world.send("/buy 100 AAPL")
    await world.send(f"/cancel {buy.order_id[:6]}")

    views = await world.desk.todays_orders(TRADE_NOW)

    assert [(view.order_id, view.status) for view in views] == [(buy.order_id, "cancelled")]


async def test_the_question_budget_lives_in_the_ledger(tmp_path, monkeypatch):
    world = await desk_world(tmp_path, monkeypatch)
    budget = bot.ManualOrdersBudget()
    day = date(2026, 10, 13)

    first = await budget.reserve(day, Decimal("2.50"))
    assert first is not None
    assert await budget.reserve(day, Decimal("0.60")) is None  # 2.50 + 0.60 > 3.00
    await budget.settle(first, Decimal("0.04"))
    assert await budget.reserve(day, Decimal("0.60")) is not None
    usage = await world.ledger.chat_usage(day)
    assert usage.spent_usd == Decimal("0.04") and usage.reserved_usd == Decimal("0.60")


async def test_check_ready_refuses_a_database_without_the_ledger(tmp_path, monkeypatch):
    from config import cfg

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "empty.db"))
    desk = bot.ManualOrdersDesk(tools=None, kill_switch=FakeKillSwitch())  # type: ignore[arg-type]

    with pytest.raises(Exception, match="no such table"):
        await desk.check_ready()


async def test_end_to_end_the_owners_order_is_queued_and_reported_a_strangers_is_not(tmp_path, monkeypatch):
    from db.core import get_db

    world = await desk_world(tmp_path, monkeypatch)
    time = FakeTime(TRADE_NOW)
    replies = FakeReplies()
    updates = FakeUpdates([[
        message(1, "/buy 100 AAPL", sent_at=TRADE_NOW - timedelta(seconds=2)),
        message(2, "/buy 100 AAPL", chat_id=STRANGER, sender=STRANGER, sent_at=TRADE_NOW),
    ]])
    assistant = TelegramAssistant(
        owner_id=OWNER, updates=updates, replies=replies, tools=world.desk.tools, desk=world.desk,
        kill_switch=world.kill, questions=None, state=AssistantState.load(tmp_path / "s" / "state.json"),
        clock=time.clock, monotonic=time.monotonic, sleep=time.sleep,
    )
    await assistant.start()
    try:
        await assistant.poll_once()
        (row,) = await world.ledger.list_active_orders()
        short = row.id[:6]
        assert replies.texts == [f"🟢 Queued BUY 100 AAPL MKT · #{short} · /cancel {short}"]
        assert row.chat_id == OWNER and row.update_id == 1

        claimed = await world.ledger.claim_next_order(TRADE_NOW + timedelta(seconds=1))
        await world.ledger.update_order_status(claimed.id, "sent", now=TRADE_NOW, ibkr_order_id=11)
        await assistant.check_orders()
        assert replies.texts[-1] == f"📤 Sent to IBKR: #{short} BUY 100 AAPL MKT"
    finally:
        await assistant.stop()

    async with get_db() as db:
        async with db.execute(
            "SELECT update_id, chat_id, kind, outcome FROM autotrader_telegram_updates ORDER BY update_id"
        ) as cursor:
            recorded = await cursor.fetchall()
    assert recorded == [
        (1, OWNER, "message/text", f"order:queued:{row.id}"),
        (2, STRANGER, "message/stranger", "dropped:stranger"),
    ]


@pytest.mark.parametrize("bad_date", [10**20, -5, 0, "yesterday", None])
async def test_an_unusable_message_date_makes_an_order_stale_and_never_crashes(harness, bad_date):
    harness.desk.parse_table["/buy 100 AAPL"] = BUY
    update = message(1, "/buy 100 AAPL")
    update["message"]["date"] = bad_date

    await harness.feed(update)

    assert harness.replies.texts == ["⏱ Stale message (unknown age) — NOT sent. Send it again if you still want it."]
    assert harness.desk.submitted == []


async def test_an_order_the_bridge_never_picks_up_is_reported_once_as_not_sent(harness):
    harness.desk.parse_table["/buy 100 AAPL"] = BUY
    harness.desk.queued = view("queued", expires_at=NOW + timedelta(seconds=88))
    await harness.feed(message(1, "/buy 100 AAPL"))

    await harness.assistant.check_orders()
    assert len(harness.replies.texts) == 1  # still within its 90 s
    harness.time.now += timedelta(seconds=100)
    await harness.assistant.check_orders()
    harness.desk.rows[ORDER_ID] = view("expired", expires_at=NOW + timedelta(seconds=88))
    await harness.assistant.check_orders()

    assert harness.replies.texts[1:] == [
        "⌛ #a1b2c3 BUY 100 AAPL MKT was not picked up by the bridge in time — it will NOT be sent "
        "(is the bridge running? /status)"
    ]
    assert harness.state.notified[ORDER_ID] == "expired"


# --------------------------------------------------------------------------
# T-1: /halt <IDENTITY> + /confirm <nonce>, /fences
# --------------------------------------------------------------------------

HALT_PROMPT = "halt ORB_V2: confirm with /confirm a1b2c3 within 60 s (stop only; resume is CLI-only)"
HALT_STOPPED = (
    "identity fence ORB_V2: STOPPED (new entries blocked; exits, flatten and manual orders unaffected)"
)


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("/halt ORB_V2", ("halt", "ORB_V2")),
        ("/HALT@AutotraderBot  ORB_V2 ", ("halt", "ORB_V2")),
        ("/confirm a1b2c3", ("confirm", "a1b2c3")),
        ("/fences", ("fences", "")),
        ("/stop AAPL 12.5", ("stop", "AAPL 12.5")),
        ("/stop", ("stop", "")),
    ],
)
def test_halt_confirm_and_fences_parse_as_their_own_commands(text, parsed):
    assert bot.split_command(text) == parsed


@pytest.mark.parametrize("given", ["ORB_V2", " ORB_V2 ", "MOMO_HOD_V2", "VWAP_PB_V2", "GAP_GO_V2"])
def test_halt_takes_exactly_a_sendable_identity(given):
    assert bot.halt_identity(given) == given.strip()


@pytest.mark.parametrize(
    "given", ["", "orb_v2", "Orb_V2", "ORB_V1", "ORB_V2 now", "ORB_V2X", "AP_MOMO_HOD_V1", "SH_ORB", "all", "AAPL 12.5"]
)
def test_halt_refuses_any_other_name(given):
    assert bot.halt_identity(given) is None


async def test_stop_with_a_symbol_and_price_is_a_stop_order_never_a_halt(harness):
    stop_order = ParsedText("order", request={"action": "SELL_STOP", "symbol": "AAPL"})
    harness.desk.parse_table["/stop AAPL 12.5"] = stop_order

    await harness.feed(message(1, "/stop AAPL 12.5"))

    assert [parsed for parsed, _ in harness.desk.submitted] == [stop_order]
    assert harness.assistant._halts.pending(OWNER) is None
    assert harness.fences.calls == [] and not harness.kill.stopped
    assert harness.desk.outcomes[1].startswith("order:queued:")


async def test_halt_then_the_right_nonce_stops_only_that_identity(harness, caplog):
    await harness.feed(message(1, "/halt ORB_V2"))

    assert harness.replies.texts == [HALT_PROMPT]
    assert harness.fences.calls == []  # nothing is written before the confirmation
    assert harness.desk.outcomes[1] == "halt:pending:ORB_V2"

    harness.time.now += timedelta(seconds=30)
    with caplog.at_level(logging.WARNING, logger="autotrader.shadow.telegram"):
        await harness.feed(message(2, "/confirm a1b2c3"))

    assert harness.fences.calls == [("telegram /halt by owner", "ORB_V2")]
    assert harness.replies.texts[-1] == HALT_STOPPED
    assert harness.desk.outcomes[2] == "halt:stopped:ORB_V2"
    incidents = [record.getMessage() for record in caplog.records if "INCIDENT" in record.getMessage()]
    assert incidents == ["INCIDENT halt identity=ORB_V2 via=telegram"]
    # Deterministic: never the order parser, never Claude, never the global switch.
    assert harness.desk.parsed == [] and harness.questions.asked == []
    assert not harness.kill.stopped and harness.kill.reasons == []
    # The request is spent: the same nonce again writes nothing.
    await harness.feed(message(3, "/confirm a1b2c3"))
    assert harness.fences.calls == [("telegram /halt by owner", "ORB_V2")]
    assert harness.replies.texts[-1].startswith("❌ No /halt is waiting")


async def test_a_wrong_nonce_writes_nothing_and_the_right_one_still_works(harness):
    for halt_id, wrong in ((1, ("/confirm d4e5f6", "/confirm")), (10, ("/confirm a1b2c3 a1b2c3", "/confirm a1b2c"))):
        harness.fences.calls.clear()
        await harness.feed(message(halt_id, "/halt ORB_V2"))
        for offset, text in enumerate(wrong, start=1):
            await harness.feed(message(halt_id + offset, text))
            assert harness.fences.calls == []
            assert harness.replies.texts[-1].startswith("❌ Wrong code for /halt ORB_V2 — nothing written.")
            assert harness.desk.outcomes[halt_id + offset] == "halt:confirm:wrong_nonce"
        assert harness.replies.texts[-1].endswith("(1 try left).")

        # A phone's capital letters are still the code.
        await harness.feed(message(halt_id + 5, "/confirm A1B2C3" if halt_id == 1 else "/confirm d4e5f6"))
        assert harness.fences.calls == [("telegram /halt by owner", "ORB_V2")]


async def test_the_third_wrong_code_drops_the_request(harness):
    """Review P1-A F-10: wrong codes were unlimited inside the 60 s window."""
    assert bot.HALT_MAX_WRONG_CODES == 3
    await harness.feed(message(1, "/halt ORB_V2"))
    await harness.feed(message(2, "/confirm 000000"), message(3, "/confirm 111111"))
    assert harness.replies.texts[-1].endswith("(1 try left).")

    await harness.feed(message(4, "/confirm 222222"))

    assert harness.desk.outcomes[4] == "halt:confirm:dropped"
    assert harness.replies.texts[-1] == (
        "❌ Wrong code for /halt ORB_V2 — nothing written. 3 wrong codes: this /halt is dropped."
        " Send /halt ORB_V2 again for a new code."
    )
    assert harness.assistant._halts.pending(OWNER) is None
    # The right code no longer finds anything to confirm.
    await harness.feed(message(5, "/confirm a1b2c3"))
    assert harness.fences.calls == [] and harness.desk.outcomes[5] == "halt:confirm:no_pending"
    # A new /halt starts a new request with its own count.
    await harness.feed(message(6, "/halt ORB_V2"), message(7, "/confirm 333333"), message(8, "/confirm d4e5f6"))
    assert harness.fences.calls == [("telegram /halt by owner", "ORB_V2")]


@pytest.mark.parametrize(("seconds", "written"), [(59, True), (60, True), (61, False), (3600, False)])
async def test_the_nonce_expires_after_sixty_seconds(harness, seconds, written):
    await harness.feed(message(1, "/halt ORB_V2"))
    harness.time.now += timedelta(seconds=seconds)

    await harness.feed(message(2, "/confirm a1b2c3"))

    if written:
        assert harness.fences.calls == [("telegram /halt by owner", "ORB_V2")]
        return
    assert harness.fences.calls == []
    assert harness.replies.texts[-1] == "⌛ /halt ORB_V2 expired — send /halt ORB_V2 again. Nothing written."
    assert harness.desk.outcomes[2] == "halt:confirm:expired"
    # An expired request is gone.
    await harness.feed(message(3, "/confirm a1b2c3"))
    assert harness.fences.calls == [] and harness.replies.texts[-1].startswith("❌ No /halt is waiting")


async def test_a_clock_that_goes_backwards_expires_the_request(harness):
    await harness.feed(message(1, "/halt ORB_V2"))
    harness.time.now -= timedelta(seconds=5)

    await harness.feed(message(2, "/confirm a1b2c3"))

    assert harness.fences.calls == [] and harness.desk.outcomes[2] == "halt:confirm:expired"


async def test_a_new_halt_replaces_the_pending_one(harness):
    await harness.feed(message(1, "/halt ORB_V2"), message(2, "/halt GAP_GO_V2"))
    assert harness.replies.texts[-1] == (
        "halt GAP_GO_V2: confirm with /confirm d4e5f6 within 60 s (stop only; resume is CLI-only)"
    )

    await harness.feed(message(3, "/confirm a1b2c3"))  # the replaced request's nonce
    assert harness.fences.calls == []
    assert harness.replies.texts[-1].startswith("❌ Wrong code for /halt GAP_GO_V2")

    await harness.feed(message(4, "/confirm d4e5f6"))
    assert harness.fences.calls == [("telegram /halt by owner", "GAP_GO_V2")]


@pytest.mark.parametrize("text", ["/halt orb_v2", "/halt ORB_V1", "/halt", "/halt ORB_V2 now", "/halt AP_MOMO_HOD_V1"])
async def test_a_name_that_is_not_sendable_is_refused_and_nothing_is_written(harness, text):
    await harness.feed(message(1, text))

    reply = harness.replies.texts[-1]
    assert reply.startswith("❌") and reply.endswith("Nothing written.")
    assert "MOMO_HOD_V2, ORB_V2, VWAP_PB_V2, GAP_GO_V2" in reply
    assert harness.desk.outcomes[1] == "halt:refused"
    assert harness.assistant._halts.pending(OWNER) is None
    await harness.feed(message(2, "/confirm a1b2c3"))
    assert harness.fences.calls == [] and harness.desk.parsed == [] and harness.questions.asked == []


async def test_confirm_without_a_pending_halt_is_refused(harness):
    await harness.feed(message(1, "/confirm a1b2c3"))

    assert harness.replies.texts == [
        "❌ No /halt is waiting for confirmation — send /halt <IDENTITY> first. Nothing written."
    ]
    assert harness.fences.calls == [] and harness.desk.outcomes[1] == "halt:confirm:no_pending"


async def test_a_halt_that_does_not_take_is_reported_with_the_mac_command(harness):
    harness.fences.error = OSError("disk I/O error")
    await harness.feed(message(1, "/halt ORB_V2"), message(2, "/confirm a1b2c3"))

    assert harness.replies.texts[-1] == (
        "🚨 identity fence ORB_V2 NOT confirmed (OSError). Run on the Mac: "
        'autotraderctl paper stop --identity ORB_V2 --reason "telegram /halt by owner"'
    )
    assert harness.desk.outcomes[2] == "halt:failed"

    harness.fences.error = None
    harness.fences.takes = False  # written but not read back as stopped
    await harness.feed(message(3, "/halt ORB_V2"), message(4, "/confirm d4e5f6"))
    assert harness.replies.texts[-1].startswith("🚨 identity fence ORB_V2 NOT confirmed. Run on the Mac:")
    assert harness.desk.outcomes[4] == "halt:unconfirmed"


async def test_a_halt_without_an_identity_store_is_refused_loudly(tmp_path):
    h = make(tmp_path)
    h.assistant._fences = None
    await h.feed(message(1, "/halt ORB_V2"), message(2, "/confirm a1b2c3"))

    assert h.replies.texts[-1].startswith("🚨 Identity fences are unavailable here — NOT stopped.")
    assert h.desk.outcomes[2] == "halt:unavailable"


async def test_there_is_no_resume_from_the_phone(harness):
    await harness.feed(message(1, "/halt ORB_V2"), message(2, "/confirm a1b2c3"))
    await harness.feed(message(3, "/resume ORB_V2"))

    assert harness.replies.texts[-1] == bot.RESUME_TEXT
    assert harness.fences.calls == [("telegram /halt by owner", "ORB_V2")]
    assert harness.fences.rows == {"ORB_V2": "telegram /halt by owner"}


async def test_halt_and_confirm_pass_the_flood_limit(harness):
    await harness.feed(*(message(n, "/hot") for n in range(1, 22)))
    assert harness.replies.texts.count(bot.FLOOD_NOTICE) == 1

    await harness.feed(message(30, "/halt ORB_V2"), message(31, "/confirm a1b2c3"))

    assert harness.fences.calls == [("telegram /halt by owner", "ORB_V2")]
    assert "/halt" in bot.FLOOD_NOTICE and "first /confirm" in bot.FLOOD_NOTICE


async def test_after_a_wrong_code_confirm_is_flood_limited_like_any_command(harness):
    """Review P1-A F-10: every /confirm bypassed the flood limit (60 wrong codes processed)."""
    await harness.feed(*(message(n, "/hot") for n in range(1, 22)))

    await harness.feed(message(30, "/halt ORB_V2"), message(31, "/confirm 000000"))
    assert harness.desk.outcomes[31] == "halt:confirm:wrong_nonce"  # the first one still goes through
    await harness.feed(*(message(n, f"/confirm {n:06x}") for n in range(32, 92)))

    assert {harness.desk.outcomes[n] for n in range(32, 92)} == {"rate_limited"}
    assert harness.fences.calls == []
    # Still pending (one wrong code), and confirmable once the flood has passed.
    assert harness.assistant._halts.pending(OWNER).wrong_codes == 1
    # A /confirm with no request waiting is limited too.
    harness.assistant._halts._pending.clear()
    await harness.feed(message(93, "/confirm a1b2c3"))
    assert harness.desk.outcomes[93] == "rate_limited"


async def test_fences_is_a_read_command(harness):
    await harness.feed(message(1, "/fences"))

    assert harness.tools.calls == ["fences"] and harness.replies.texts == ["fences text"]


def test_help_lists_halt_and_fences_and_says_resume_is_at_the_mac():
    for words in ("/fences", "/halt ORB_V2", "/confirm", "stop only", "autotraderctl"):
        assert words in bot.HELP_TEXT


def test_halt_requests_refuse_a_bad_name_or_nonce_at_the_source():
    requests = bot.HaltRequests(clock=lambda: NOW, new_nonce=lambda: "a1b2c3")
    with pytest.raises(ValueError):
        requests.request(OWNER, "ORB_V1")
    bad = bot.HaltRequests(clock=lambda: NOW, new_nonce=lambda: "")
    with pytest.raises(ValueError):
        bad.request(OWNER, "ORB_V2")
    assert len(bot.new_halt_nonce()) == 6 and int(bot.new_halt_nonce(), 16) >= 0


class CeilingClock:
    """The composition-root tests' clock: it moves only when the code under test
    sleeps, and never past ``ceiling``. Unbounded, the background watch's
    30-second sleeps raced it through months of leads while the poll loop
    waited on real time. At the ceiling a sleep yields to the event loop only;
    ``stalled`` says the test's window has passed (the run is then ended)."""

    def __init__(self, start: datetime, *, ceiling: datetime) -> None:
        self.now = start
        self.ceiling = ceiling
        self._stalled_sleeps = 0

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        if self.now >= self.ceiling:
            self._stalled_sleeps += 1
        self.now = min(self.ceiling, self.now + timedelta(seconds=max(seconds, 0.0)))
        await asyncio.sleep(0)

    def stalled(self) -> bool:
        return self._stalled_sleeps > 0


async def run_bounded(run: Any, seconds: float = 10.0) -> Any:
    """A composition-root run that ends by itself; a hang fails at once, plainly."""
    try:
        return await asyncio.wait_for(run, seconds)
    except asyncio.TimeoutError:
        pytest.fail(f"the assistant did not stop within {seconds:g} s (a lost cancellation?)")


async def fence_database(tmp_path: Path, monkeypatch) -> Path:
    from config import cfg
    from db.core import init_db

    path = tmp_path / "fences.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


async def test_acceptance_halt_and_confirm_write_the_identity_row_the_bridge_reads(tmp_path, monkeypatch):
    """Scratch DB: /halt ORB_V2 + /confirm -> paper:ORB_V2 stopped by the real store; the
    pinned bridge's own predicate (loaded read-only, unchanged) sees it; the global row is untouched."""
    import importlib.util

    from trading.storage.runtime_fence import SQLitePaperKillStore

    database = await fence_database(tmp_path, monkeypatch)
    h = make(tmp_path)
    store = SQLitePaperKillStore()
    h.assistant._fences = store

    await h.feed(message(1, "/halt ORB_V2"), message(2, "/confirm a1b2c3"))

    assert h.replies.texts == [HALT_PROMPT, HALT_STOPPED]
    assert [(name, stopped, reason) for name, stopped, reason, _at in await store.identity_rows()] == [
        ("ORB_V2", True, "telegram /halt by owner")
    ]
    assert await store.is_stopped(identity="ORB_V2") and not await store.is_stopped()
    bridge_path = Path(__file__).resolve().parents[1] / "scripts_local" / "paper_bridge.py"
    spec = importlib.util.spec_from_file_location("paper_bridge_t1_acceptance", bridge_path)
    assert spec is not None and spec.loader is not None
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge
    try:
        spec.loader.exec_module(bridge)
        assert bridge.identity_fence_active(database, "ORB_V2")
        assert bridge.fenced_identities(database) == ["ORB_V2"]
        assert not bridge.paper_kill_switch_active(database)
    finally:
        sys.modules.pop(spec.name, None)


async def test_end_to_end_the_composition_root_wires_the_real_fence_store(tmp_path, monkeypatch):
    """run_telegram_assistant -> _serve with a mock Bot API: the store it builds is the one /confirm writes."""
    import httpx

    from trading.operations.keychain import KeychainReadError
    from trading.storage.runtime_fence import SQLitePaperKillStore

    await fence_database(tmp_path, monkeypatch)
    sent: list[str] = []
    stage = {"n": 0}

    def ok(result: Any) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": result})

    async def handle(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        if method == "sendMessage":
            sent.append(payload["text"])
            return ok({"message_id": len(sent)})
        assert method == "getUpdates", method
        await asyncio.sleep(0.01)  # let the reply sender run
        now = datetime.now(UTC)
        if stage["n"] == 0:
            stage["n"] = 1
            return ok([message(1, "/halt ORB_V2", sent_at=now)])
        if stage["n"] == 1:
            prompts = [text for text in sent if text.startswith("halt ORB_V2: confirm with /confirm ")]
            if not prompts:
                return ok([])
            stage["n"] = 2
            nonce = prompts[0].split("/confirm ", 1)[1].split()[0]
            return ok([message(2, f"/confirm {nonce}", sent_at=now)])
        if not any(text == HALT_STOPPED for text in sent):
            return ok([])
        return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})

    def no_key() -> str:
        raise KeychainReadError("no key in this test")

    environment = env(tmp_path, transport=httpx.MockTransport(handle), read_anthropic_key=no_key)
    save_chat_id(telegram_config_file(environment.paths), OWNER)

    assert await asyncio.wait_for(bot.run_telegram_assistant(env=environment), 20) == bot.EXIT_CONFIG
    assert HALT_STOPPED in sent
    rows = await SQLitePaperKillStore().identity_rows()
    assert [(name, stopped, reason) for name, stopped, reason, _at in rows] == [
        ("ORB_V2", True, "telegram /halt by owner")
    ]
    assert not await SQLitePaperKillStore().is_stopped()


async def test_end_to_end_the_composition_root_runs_the_overnight_watch(tmp_path, monkeypatch):
    """T-4 through run_telegram_assistant -> _serve: the always-on child's own
    watch, with the environment's clock, sleep and Gateway probe, pages a port
    closed at the 05:55 ET lead through the real sender to the pinned chat."""
    import httpx

    from trading.operations.keychain import KeychainReadError

    await fence_database(tmp_path, monkeypatch)
    monday = datetime(2026, 10, 19, 5, 50, tzinfo=ET).astimezone(UTC)
    probes: list[datetime] = []
    sent: list[tuple[int, str]] = []

    async def closed() -> bool:
        probes.append(clock.now)
        return False

    clock = CeilingClock(monday, ceiling=monday + timedelta(minutes=6))

    page = (
        "⛔ IB Gateway closed since 05:50 ET (its API port does not answer) — log in to IB Gateway;"
        " session starts 07:55 ET (05:55 ET check)"
    )

    async def handle(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        if method == "sendMessage":
            sent.append((payload["chat_id"], payload["text"]))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})
        await asyncio.sleep(0)
        if any(text == page for _chat, text in sent) or clock.stalled():
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        return httpx.Response(200, json={"ok": True, "result": []})

    def no_key() -> str:
        raise KeychainReadError("no key in this test")

    environment = env(
        tmp_path,
        transport=httpx.MockTransport(handle),
        read_anthropic_key=no_key,
        clock=clock.clock,
        sleep=clock.sleep,
        gateway_probe=closed,
    )
    save_chat_id(telegram_config_file(environment.paths), OWNER)

    assert await run_bounded(bot.run_telegram_assistant(env=environment)) == bot.EXIT_CONFIG
    assert (OWNER, page) in sent, f"no 05:55 page; sent {sent}, clock {clock.now}"
    assert probes[:2] == [monday, monday + timedelta(minutes=5)]


async def test_stop_cancels_again_a_background_task_that_lost_its_first_cancel(tmp_path, monkeypatch):
    """Python 3.11's wait_for can drop a cancellation (gh-86296); stop() cancels a
    survivor again, so a shutdown or a fatal poll error never hangs on it."""
    monkeypatch.setattr(bot, "STOP_RECANCEL_SECONDS", 0.01)
    cancels: list[int] = []

    class Stubborn:
        async def run(self, send, save) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancels.append(1)  # dropped, as wait_for does when its inner task just finished
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancels.append(2)
                raise

    h = make(tmp_path)
    h.assistant._overnight = Stubborn()
    await h.assistant.start()
    await asyncio.sleep(0)

    await asyncio.wait_for(h.assistant.stop(), 5)

    assert cancels == [1, 2]
