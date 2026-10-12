"""Telegram delivery: plain text, pacing, retry_after, splitting, chat linking, no token leaks."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import stat
import subprocess

import httpx
import pytest

from trading.operations.keychain import (
    TELEGRAM_KEYCHAIN_ACCOUNT,
    TELEGRAM_KEYCHAIN_SERVICE,
    KeychainReadError,
    MacOSKeychainReader,
    read_telegram_bot_token,
)
from trading.operations.notifier import (
    MIN_SEND_INTERVAL_SECONDS,
    TELEGRAM_MESSAGE_LIMIT,
    TelegramAPIError,
    TelegramBotAPI,
    TelegramConfigError,
    TelegramNotifier,
    chat_from_updates,
    discover_chat,
    is_valid_bot_token,
    load_chat_id,
    save_chat_id,
    split_message,
    telegram_config_file,
    utf16_length,
)
from trading.operations.paths import PathSecurityError, UserPaths

TOKEN = "123456789:AAH_test-token-value_0123456789abcdef"
EPSILON = 1e-9

Reply = httpx.Response | Callable[[httpx.Request], httpx.Response]


class FakeTime:
    """A monotonic clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(float(seconds), 0.0)
        await asyncio.sleep(0)


class FakeTelegram:
    """Records every Bot API call; scripted replies first, then success."""

    def __init__(self, clock: FakeTime | None = None, replies: list[Reply] | None = None) -> None:
        self.clock = clock
        self.replies = list(replies or [])
        self.updates: list[dict] = []
        self.calls: list[tuple[str, str, dict, float | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append(
            (request.url.path, method, payload, self.clock.monotonic() if self.clock else None)
        )
        if self.replies:
            reply = self.replies.pop(0)
            return reply(request) if callable(reply) else reply
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": self.updates})
        if method == "getMe":
            return httpx.Response(
                200, json={"ok": True, "result": {"id": 1, "is_bot": True, "username": "at_bot"}}
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.calls)}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def sent_texts(self) -> list[str]:
        return [payload["text"] for _, method, payload, _ in self.calls if method == "sendMessage"]

    def times(self) -> list[float]:
        return [moment for *_, moment in self.calls if moment is not None]


def _error(status: int, description: str, **parameters: object) -> httpx.Response:
    body: dict[str, object] = {"ok": False, "error_code": status, "description": description}
    if parameters:
        body["parameters"] = parameters
    return httpx.Response(status, json=body)


def _gaps(times: list[float]) -> list[float]:
    return [later - earlier for earlier, later in zip(times, times[1:])]


@pytest.mark.anyio
async def test_messages_are_plain_text_posted_to_the_token_path_only():
    telegram = FakeTelegram()
    text = "🛑 ORB_V1 · MMED · risk blocked\nRisk codes: max_open_positions, symbol_already_active"
    async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
        await api.send_message(42, text)

    [(path, method, payload, _)] = telegram.calls
    assert path == f"/bot{TOKEN}/sendMessage"
    assert method == "sendMessage"
    # No parse_mode: underscores in reason codes stay literal.
    assert payload == {"chat_id": 42, "text": text}


def test_long_messages_split_on_line_boundaries_within_telegrams_utf16_limit():
    lines = [f"line {index:04d} 💵 FILL BF B: 3741/3741 sh @ 26.7496 (momo)" for index in range(220)]
    parts = split_message("\n".join(lines))

    assert 1 < len(parts) <= 4
    assert all(utf16_length(part) <= TELEGRAM_MESSAGE_LIMIT for part in parts)
    assert [part.split(" ", 1)[0] for part in parts] == [
        f"({index}/{len(parts)})" for index in range(1, len(parts) + 1)
    ]
    rejoined = "\n".join(part.split(" ", 1)[1] for part in parts)
    assert rejoined.split("\n") == lines


def test_unbreakable_and_oversized_messages_are_cut_safely_and_marked():
    emoji_wall = "💵" * 5_000  # 10,000 UTF-16 units, no spaces or newlines
    parts = split_message(emoji_wall)
    assert all(utf16_length(part) <= TELEGRAM_MESSAGE_LIMIT for part in parts)
    assert sum(part.count("💵") for part in parts) == 5_000

    flood = split_message("word " * 20_000)
    assert len(flood) == 4
    assert all(utf16_length(part) <= TELEGRAM_MESSAGE_LIMIT for part in flood)
    assert flood[-1].endswith("… (truncated)")
    assert split_message("short") == ["short"]


@pytest.mark.anyio
async def test_one_sender_spaces_every_request_by_at_least_1_1_seconds():
    clock = FakeTime()
    telegram = FakeTelegram(clock)
    async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
        notifier = TelegramNotifier(api, chat_id=42, sleep=clock.sleep, monotonic=clock.monotonic)
        results = await asyncio.gather(*(notifier.notify(f"message {index}") for index in range(4)))
        await notifier.aclose()

    assert results == [True, True, True, True]
    assert telegram.sent_texts() == [f"message {index}" for index in range(4)]
    assert all(gap >= MIN_SEND_INTERVAL_SECONDS - EPSILON for gap in _gaps(telegram.times()))
    with pytest.raises(ValueError):
        TelegramNotifier(api, chat_id=42, min_interval_seconds=0.5)


@pytest.mark.anyio
async def test_rate_limit_retry_after_is_honoured_before_any_further_request():
    clock = FakeTime()
    telegram = FakeTelegram(
        clock, replies=[_error(429, "Too Many Requests: retry after 7", retry_after=7)]
    )
    async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
        notifier = TelegramNotifier(api, chat_id=42, sleep=clock.sleep, monotonic=clock.monotonic)
        results = await asyncio.gather(notifier.notify("first"), notifier.notify("second"))
        await notifier.aclose()

    assert results == [True, True]
    assert telegram.sent_texts() == ["first", "first", "second"]
    gaps = _gaps(telegram.times())
    assert gaps[0] >= 7 - EPSILON
    assert gaps[1] >= MIN_SEND_INTERVAL_SECONDS - EPSILON


@pytest.mark.anyio
async def test_a_failing_message_is_dropped_after_three_attempts_and_the_sender_survives():
    clock = FakeTime()
    telegram = FakeTelegram(clock, replies=[_error(502, "Bad Gateway")] * 3)
    async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
        notifier = TelegramNotifier(api, chat_id=42, sleep=clock.sleep, monotonic=clock.monotonic)
        first = notifier.notify("first")
        second = notifier.notify("second")
        assert await first is False
        assert await second is True
        await notifier.aclose()

    assert telegram.sent_texts() == ["first", "first", "first", "second"]
    gaps = _gaps(telegram.times())
    assert gaps[0] >= 2 - EPSILON and gaps[1] >= 5 - EPSILON


@pytest.mark.anyio
async def test_permanent_rejections_are_not_retried():
    clock = FakeTime()
    telegram = FakeTelegram(clock, replies=[_error(400, "Bad Request: chat not found")])
    async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
        notifier = TelegramNotifier(api, chat_id=42, sleep=clock.sleep, monotonic=clock.monotonic)
        assert await notifier.notify("hello") is False
        await notifier.aclose()

    assert telegram.sent_texts() == ["hello"]


@pytest.mark.anyio
async def test_timeouts_and_network_errors_retry_then_drop_without_raising():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    clock = FakeTime()
    telegram = FakeTelegram(clock, replies=[timeout, refused, timeout])
    async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
        notifier = TelegramNotifier(api, chat_id=42, sleep=clock.sleep, monotonic=clock.monotonic)
        assert await notifier.notify("hello") is False
        assert await notifier.notify("") is False  # empty text is refused, not sent
        await notifier.aclose()
        late = notifier.notify("after close")

    assert late.done() and late.result() is False
    assert telegram.sent_texts() == ["hello", "hello", "hello"]


@pytest.mark.anyio
async def test_closing_drains_queued_messages_and_splits_long_ones():
    clock = FakeTime()
    telegram = FakeTelegram(clock)
    long_text = "\n".join(f"report line {index:05d} with some detail" for index in range(400))
    async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
        notifier = TelegramNotifier(api, chat_id=42, sleep=clock.sleep, monotonic=clock.monotonic)
        pending = [notifier.notify("one"), notifier.notify(long_text), notifier.notify("two")]
        await notifier.aclose()

    assert [future.result() for future in pending] == [True, True, True]
    texts = telegram.sent_texts()
    assert texts[0] == "one" and texts[-1] == "two"
    assert len(texts) > 3
    assert all(utf16_length(text) <= TELEGRAM_MESSAGE_LIMIT for text in texts)


@pytest.mark.anyio
async def test_the_bot_token_never_reaches_logs_or_error_text():
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = Capture(level=logging.DEBUG)
    root = logging.getLogger()
    notify_logger = logging.getLogger("autotrader.shadow.notify")
    previous_level = root.level
    root.addHandler(capture)
    notify_logger.addHandler(capture)
    root.setLevel(logging.DEBUG)
    clock = FakeTime()
    secret_half = TOKEN.split(":", 1)[1]
    telegram = FakeTelegram(
        clock,
        replies=[
            _error(400, f"Bad Request: echoing {TOKEN}"),
            _error(500, f"Internal error at /bot{TOKEN.replace(':', '%3A')}/sendMessage"),
            httpx.Response(200, json={"ok": True, "result": {}}),
            _error(401, f"Unauthorized {TOKEN}"),
        ],
    )
    try:
        async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
            notifier = TelegramNotifier(
                api, chat_id=42, sleep=clock.sleep, monotonic=clock.monotonic
            )
            assert await notifier.notify("first") is False
            assert await notifier.notify("second") is True
            await notifier.aclose()
            with pytest.raises(TelegramAPIError) as raised:
                await api.get_updates()
    finally:
        root.removeHandler(capture)
        notify_logger.removeHandler(capture)
        root.setLevel(previous_level)

    rendered = [record.getMessage() for record in records]
    assert any("[REDACTED]" in message for message in rendered)
    # The secret half is redacted too, so a URL-encoded token cannot slip through.
    assert all(secret_half not in message for message in rendered)
    # httpx would log the full request URL at INFO; it is held at WARNING.
    assert all("api.telegram.org" not in message for message in rendered)
    assert secret_half not in str(raised.value)
    assert raised.value.status == 401 and raised.value.permanent


def test_token_shape_is_validated_without_echoing_the_value():
    assert is_valid_bot_token(TOKEN)
    for bad in ("", "abc", "123:short", f"{TOKEN}/x", "123456789:AAH/../../../etc/passwd-xx", f" {TOKEN}"):
        assert not is_valid_bot_token(bad)
    with pytest.raises(ValueError) as raised:
        TelegramBotAPI("123456789:not a token at all, really")
    assert "not a token" not in str(raised.value)


def test_newest_private_start_links_the_chat_and_groups_are_ignored():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 111, "type": "private", "username": "old"}, "text": "/start"}},
        {"update_id": 2, "message": {"chat": {"id": -500, "type": "group", "title": "desk"}, "text": "/start"}},
        {"update_id": 3, "message": {"chat": {"id": 222, "type": "private", "first_name": "Owner"}, "text": "hello"}},
        {"update_id": 4, "message": {"chat": {"id": 333, "type": "private", "first_name": "Owner"}, "text": "/start@at_bot"}},
        {"update_id": 5, "edited_message": {"chat": {"id": 444, "type": "private"}, "text": "/start"}},
    ]

    chat = chat_from_updates(updates)

    assert chat is not None and chat.chat_id == 333 and chat.label == "Owner"
    assert chat_from_updates(updates[1:3]) is None
    assert chat_from_updates([]) is None


@pytest.mark.anyio
async def test_discovery_reads_get_updates():
    telegram = FakeTelegram()
    telegram.updates = [
        {"update_id": 9, "message": {"chat": {"id": 77, "type": "private", "username": "owner"}, "text": "/start"}}
    ]
    async with TelegramBotAPI(TOKEN, transport=telegram.transport) as api:
        chat = await discover_chat(api)

    assert chat is not None and chat.chat_id == 77 and chat.label == "@owner"
    [(_, method, payload, _)] = telegram.calls
    assert method == "getUpdates" and payload["allowed_updates"] == ["message"]


def test_chat_id_cache_is_private_and_holds_only_the_chat_id(tmp_path):
    paths = UserPaths(home=tmp_path / "home")
    path = telegram_config_file(paths)
    assert path == tmp_path / "home" / ".config" / "autotrader-v1" / "telegram.json"
    assert load_chat_id(path) is None

    save_chat_id(path, 333)

    assert json.loads(path.read_text(encoding="utf-8")) == {"chat_id": 333}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    path.chmod(0o644)
    assert load_chat_id(path) == 333
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        save_chat_id(path, 0)

    path.write_text('{"chat_id": true}', encoding="utf-8")
    with pytest.raises(TelegramConfigError):
        load_chat_id(path)
    link = path.with_name("linked.json")
    link.symlink_to(path)
    with pytest.raises(PathSecurityError):
        load_chat_id(link)


def test_telegram_token_is_read_from_its_own_keychain_item():
    commands: list[tuple[str, ...]] = []

    def runner(command):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout=f"{TOKEN}\n", stderr="")

    assert read_telegram_bot_token(MacOSKeychainReader(command_runner=runner)) == TOKEN
    assert TELEGRAM_KEYCHAIN_SERVICE == "com.maccabi.autotrader-v1.telegram"
    assert TELEGRAM_KEYCHAIN_ACCOUNT == "telegram-bot-token"
    assert commands == [
        (
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            "telegram-bot-token",
            "-s",
            "com.maccabi.autotrader-v1.telegram",
            "-w",
        )
    ]

    def missing(command):
        return subprocess.CompletedProcess(command, 44, stdout="", stderr="could not be found")

    with pytest.raises(KeychainReadError):
        read_telegram_bot_token(MacOSKeychainReader(command_runner=missing))
