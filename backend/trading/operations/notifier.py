"""Telegram Bot API delivery to the owner's phone.

Every message goes through one sender task, in order, as plain text (no
Markdown or HTML: reason codes contain underscores), at most one request per
1.1 s, honouring Telegram's 429 ``retry_after``. Callers enqueue and never see
an exception; a message that still fails after three attempts is logged and
dropped.

The bot token is a credential that travels in the request path
(``/bot<token>/<method>``). No log line here contains a URL, httpx's own
request logging (which prints full URLs at INFO) is held at WARNING and
scrubbed, and Bot API error text is redacted before it is logged or raised.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
import re
import time
from types import TracebackType
from typing import Any, Protocol

import httpx

from trading.operations.paths import UserPaths, atomic_private_write, ensure_private_file


logger = logging.getLogger("autotrader.shadow.notify")

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_CONFIG_FILENAME = "telegram.json"
# Telegram counts its 4096-character limit in UTF-16 code units, so an emoji
# outside the Basic Multilingual Plane uses two of them.
TELEGRAM_MESSAGE_LIMIT = 4096
MAX_MESSAGE_PARTS = 4
# Telegram allows about one message per second into one chat.
MIN_SEND_INTERVAL_SECONDS = 1.1
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_SEND_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2.0, 5.0)
# A pathological retry_after must not silence the phone for hours.
MAX_RETRY_AFTER_SECONDS = 600.0
MAX_QUEUED_MESSAGES = 500
DEFAULT_DRAIN_TIMEOUT_SECONDS = 15.0
REDACTED = "[REDACTED]"
TRUNCATION_MARKER = "\n… (truncated)"

_TOKEN_SHAPE = re.compile(r"[0-9]{3,20}:[A-Za-z0-9_-]{20,128}")
_METHOD_SHAPE = re.compile(r"[A-Za-z]{3,32}")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Room for the "(i/n) " prefix on split messages.
_PART_PREFIX_RESERVE = 12
_CONFIG_MAX_BYTES = 4096
_HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")


class TelegramAPIError(RuntimeError):
    """A Bot API call failed. The message never contains the token or a URL."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
        permanent: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.permanent = permanent


class TelegramConfigError(RuntimeError):
    """The cached Telegram chat configuration cannot be used."""


class MessageSender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class TelegramChat:
    chat_id: int
    label: str


def is_valid_bot_token(token: str) -> bool:
    """BotFather tokens are ``<bot id>:<secret>``; the shape also keeps the URL path clean."""
    return isinstance(token, str) and _TOKEN_SHAPE.fullmatch(token) is not None


def token_secrets(token: str) -> tuple[str, ...]:
    """Strings to redact: the token and its secret half, which survives URL-encoding."""
    secret = token.split(":", 1)[1] if ":" in token else ""
    return tuple(value for value in (token, secret) if len(value) >= 20)


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


def normalize_text(text: object) -> str:
    """Plain text Telegram accepts: valid UTF-8, no terminal escapes or control bytes."""
    rendered = str(text).encode("utf-8", "replace").decode("utf-8")
    rendered = _ANSI_ESCAPE.sub("", rendered.replace("\r\n", "\n"))
    return _CONTROL_CHARACTERS.sub("", rendered).strip()


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _fitting_prefix(text: str, limit: int) -> int:
    """Length (in code points) of the longest prefix within ``limit`` UTF-16 units."""
    units = 0
    for index, character in enumerate(text):
        units += 2 if ord(character) > 0xFFFF else 1
        if units > limit:
            return index
    return len(text)


def split_message(
    text: str,
    *,
    limit: int = TELEGRAM_MESSAGE_LIMIT,
    max_parts: int = MAX_MESSAGE_PARTS,
) -> list[str]:
    """Split ``text`` into numbered parts that each fit Telegram's limit.

    Breaks prefer a line boundary, then a space. Anything beyond ``max_parts``
    is cut with a visible marker rather than flooding the chat.
    """
    if limit <= _PART_PREFIX_RESERVE * 4 or max_parts < 1:
        raise ValueError("message limit or part count is too small")
    if utf16_length(text) <= limit:
        return [text]
    body_limit = limit - _PART_PREFIX_RESERVE
    chunks: list[str] = []
    remaining = text
    while remaining:
        if utf16_length(remaining) <= body_limit:
            chunks.append(remaining)
            break
        cut = _fitting_prefix(remaining, body_limit)
        window = remaining[:cut]
        boundary = window.rfind("\n")
        if boundary < cut // 2:
            boundary = window.rfind(" ")
        if boundary >= cut // 2:
            cut = boundary + 1
        piece = remaining[:cut].rstrip()
        if piece:
            chunks.append(piece)
        remaining = remaining[cut:].lstrip()
    if len(chunks) > max_parts:
        chunks = chunks[:max_parts]
        room = body_limit - utf16_length(TRUNCATION_MARKER)
        last = chunks[-1]
        chunks[-1] = last[: _fitting_prefix(last, room)].rstrip() + TRUNCATION_MARKER
    if len(chunks) == 1:
        return chunks
    total = len(chunks)
    return [f"({index}/{total}) {chunk}" for index, chunk in enumerate(chunks, start=1)]


class _TokenRedactingFilter(logging.Filter):
    """Scrub the token from any record a third-party HTTP logger emits."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self._secrets = token_secrets(token)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a record that cannot render is dropped, not leaked
            return False
        redacted = _redact(message, self._secrets)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def _guard_http_client_logs(redactor: logging.Filter) -> None:
    for name in _HTTP_CLIENT_LOGGERS:
        http_logger = logging.getLogger(name)
        # httpx logs every request line at INFO, and the line carries the token.
        if http_logger.level < logging.WARNING:
            http_logger.setLevel(logging.WARNING)
        http_logger.addFilter(redactor)


def _release_http_client_logs(redactor: logging.Filter) -> None:
    for name in _HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).removeFilter(redactor)


def _retry_after_seconds(body: Mapping[str, Any]) -> float:
    parameters = body.get("parameters")
    value = parameters.get("retry_after") if isinstance(parameters, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        return 1.0
    return seconds


class TelegramBotAPI:
    """Minimal Bot API client; the token exists only in the request path."""

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not is_valid_bot_token(token):
            raise ValueError("the Telegram bot token does not have the expected shape")
        self._token = token
        self._secrets = token_secrets(token)
        self._timeout_seconds = timeout_seconds
        self._redactor = _TokenRedactingFilter(token)
        _guard_http_client_logs(self._redactor)
        self._client = httpx.AsyncClient(
            base_url=TELEGRAM_API_BASE_URL,
            transport=transport,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._closed = False

    async def __aenter__(self) -> TelegramBotAPI:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._client.aclose()
        finally:
            _release_http_client_logs(self._redactor)

    def redact(self, text: str) -> str:
        return _redact(text, self._secrets)

    async def send_message(self, chat_id: int, text: str) -> None:
        # No parse_mode: plain text cannot be mangled by Markdown or HTML parsing.
        await self.call("sendMessage", {"chat_id": chat_id, "text": text})

    async def get_me(self) -> Mapping[str, Any]:
        result = await self.call("getMe", {})
        if not isinstance(result, Mapping):
            raise TelegramAPIError("getMe returned an unexpected result")
        return result

    async def get_updates(self) -> list[Mapping[str, Any]]:
        result = await self.call("getUpdates", {"timeout": 0, "allowed_updates": ["message"]})
        if not isinstance(result, list):
            raise TelegramAPIError("getUpdates returned an unexpected result")
        return [update for update in result if isinstance(update, Mapping)]

    async def call(self, method: str, payload: Mapping[str, Any]) -> Any:
        if not _METHOD_SHAPE.fullmatch(method):
            raise ValueError("invalid Bot API method name")
        try:
            response = await self._client.post(f"/bot{self._token}/{method}", json=dict(payload))
        except httpx.TimeoutException:
            raise TelegramAPIError(
                f"{method} timed out after {self._timeout_seconds:.0f} s"
            ) from None
        except httpx.HTTPError as exc:
            # httpx errors carry the request, and so the URL: keep only the type.
            raise TelegramAPIError(f"{method} failed: {type(exc).__name__}") from None
        return self._result(method, response)

    def _result(self, method: str, response: httpx.Response) -> Any:
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, Mapping):
            body = {}
        if status == 200 and body.get("ok") is True:
            return body.get("result")
        description = body.get("description") or response.reason_phrase or "no description"
        description = normalize_text(self.redact(str(description)))[:200]
        if status == 429 or body.get("error_code") == 429:
            raise TelegramAPIError(
                f"{method} rate limited: {description}",
                status=429,
                retry_after=_retry_after_seconds(body),
            )
        # Other 4xx answers (bad request, revoked token, blocked bot, unknown
        # chat) fail the same way on every retry.
        raise TelegramAPIError(
            f"{method} failed: HTTP {status}: {description}",
            status=status,
            permanent=400 <= status < 500,
        )


def telegram_config_file(paths: UserPaths) -> Path:
    return paths.config_directory / TELEGRAM_CONFIG_FILENAME


def _is_chat_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value != 0


def load_chat_id(path: Path) -> int | None:
    """The cached chat id, or None before the first successful discovery."""
    target = path.expanduser()
    if not target.exists() and not target.is_symlink():
        return None
    # Rejects symlinks and non-regular files; re-tightens the mode to 0600.
    ensure_private_file(target)
    with target.open("rb") as stream:
        raw = stream.read(_CONFIG_MAX_BYTES + 1)
    if len(raw) > _CONFIG_MAX_BYTES:
        raise TelegramConfigError(f"{target.name} is larger than expected")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise TelegramConfigError(f"{target.name} is not valid JSON") from exc
    chat_id = data.get("chat_id") if isinstance(data, dict) else None
    if not _is_chat_id(chat_id):
        raise TelegramConfigError(f"{target.name} does not contain a valid chat_id")
    return chat_id


def save_chat_id(path: Path, chat_id: int) -> Path:
    if not _is_chat_id(chat_id):
        raise ValueError("invalid Telegram chat id")
    return atomic_private_write(path, json.dumps({"chat_id": chat_id}).encode("utf-8"))


def _chat_label(chat: Mapping[str, Any], chat_id: int) -> str:
    username = chat.get("username")
    if isinstance(username, str) and username.strip():
        return normalize_text("@" + username.strip())[:64]
    first_name = chat.get("first_name")
    if isinstance(first_name, str) and first_name.strip():
        return normalize_text(first_name)[:64]
    return f"chat {chat_id}"


def chat_from_updates(updates: Sequence[Mapping[str, Any]]) -> TelegramChat | None:
    """The private chat that most recently sent /start to the bot."""
    for update in reversed(updates):
        message = update.get("message")
        if not isinstance(message, Mapping):
            continue
        chat = message.get("chat")
        text = message.get("text")
        if not isinstance(chat, Mapping) or not isinstance(text, str):
            continue
        # Trading events go to a person, never to a group the bot was added to.
        if chat.get("type") != "private":
            continue
        words = text.split()
        if not words or words[0].split("@", 1)[0] != "/start":
            continue
        chat_id = chat.get("id")
        if not _is_chat_id(chat_id):
            continue
        return TelegramChat(chat_id=chat_id, label=_chat_label(chat, chat_id))
    return None


async def discover_chat(api: TelegramBotAPI) -> TelegramChat | None:
    return chat_from_updates(await api.get_updates())


@dataclass(slots=True)
class _Outgoing:
    text: str
    done: asyncio.Future[bool]


def _resolve(future: asyncio.Future[bool], delivered: bool) -> None:
    if not future.done():
        future.set_result(delivered)


class TelegramNotifier:
    """The single sender: every message to the owner goes through one queue.

    ``notify`` never raises; its future resolves True once Telegram accepted
    every part of the message, or False when the message was dropped.
    """

    def __init__(
        self,
        sender: MessageSender,
        *,
        chat_id: int,
        min_interval_seconds: float = MIN_SEND_INTERVAL_SECONDS,
        max_attempts: int = MAX_SEND_ATTEMPTS,
        max_queued: int = MAX_QUEUED_MESSAGES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not _is_chat_id(chat_id):
            raise ValueError("invalid Telegram chat id")
        if min_interval_seconds < MIN_SEND_INTERVAL_SECONDS:
            raise ValueError(f"send spacing must be at least {MIN_SEND_INTERVAL_SECONDS} s")
        if max_attempts < 1 or max_queued < 1:
            raise ValueError("max_attempts and max_queued must be positive")
        self._sender = sender
        self._chat_id = chat_id
        self._min_interval = min_interval_seconds
        self._max_attempts = max_attempts
        self._max_queued = max_queued
        self._sleep = sleep
        self._monotonic = monotonic
        self._queue: asyncio.Queue[_Outgoing | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._last_attempt = float("-inf")
        self._not_before = float("-inf")

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    def notify(self, text: str) -> asyncio.Future[bool]:
        """Queue ``text`` for delivery. Must be called from the running event loop."""
        done: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        try:
            message = normalize_text(text)
            if self._closing:
                reason = "the notifier is closed"
            elif not message:
                reason = "the message is empty"
            elif self._queue.qsize() >= self._max_queued:
                reason = f"{self._max_queued} messages are already queued"
            else:
                self._queue.put_nowait(_Outgoing(text=message, done=done))
                self._ensure_sender()
                return done
            logger.warning("Telegram message not queued: %s", reason)
        except Exception as exc:  # noqa: BLE001 - callers must never see a notifier failure
            logger.error("Telegram message not queued: %s", type(exc).__name__)
        _resolve(done, False)
        return done

    async def aclose(self, *, drain_timeout: float = DEFAULT_DRAIN_TIMEOUT_SECONDS) -> None:
        """Stop accepting messages and deliver what is queued within ``drain_timeout``."""
        self._closing = True
        task = self._task
        if task is not None and not task.done():
            self._queue.put_nowait(None)
            finished, _ = await asyncio.wait({task}, timeout=drain_timeout)
            if not finished:
                logger.warning(
                    "Telegram queue not drained within %.0f s; dropping %d message(s)",
                    drain_timeout,
                    self._queue.qsize(),
                )
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        while not self._queue.empty():
            leftover = self._queue.get_nowait()
            if leftover is not None:
                _resolve(leftover.done, False)

    def _ensure_sender(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="telegram-sender"
            )

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            delivered = False
            try:
                delivered = await self._deliver(item.text)
            except asyncio.CancelledError:
                _resolve(item.done, False)
                raise
            except Exception as exc:  # noqa: BLE001 - one bad message never stops the sender
                logger.error("Telegram sender error: %s", type(exc).__name__)
            _resolve(item.done, delivered)

    async def _deliver(self, text: str) -> bool:
        parts = split_message(text)
        for part in parts:
            if not await self._send_part(part):
                return False
        logger.debug("Telegram message delivered in %d part(s)", len(parts))
        return True

    async def _wait_turn(self) -> None:
        ready_at = max(self._last_attempt + self._min_interval, self._not_before)
        delay = ready_at - self._monotonic()
        if delay > 0:
            await self._sleep(delay)

    async def _send_part(self, part: str) -> bool:
        for attempt in range(1, self._max_attempts + 1):
            await self._wait_turn()
            try:
                await self._sender.send_message(self._chat_id, part)
            except TelegramAPIError as exc:
                self._last_attempt = self._monotonic()
                if exc.retry_after is not None:
                    # Flood control applies to the whole bot, so every queued
                    # message waits, not just this one.
                    wait = min(max(exc.retry_after, 0.0), MAX_RETRY_AFTER_SECONDS)
                    self._not_before = self._last_attempt + wait
                    logger.warning(
                        "Telegram asked to slow down: waiting %.0f s (attempt %d of %d)",
                        wait,
                        attempt,
                        self._max_attempts,
                    )
                    continue
                if exc.permanent:
                    logger.error("Telegram rejected a message: %s; dropped", exc)
                    return False
                logger.warning(
                    "Telegram send failed: %s (attempt %d of %d)", exc, attempt, self._max_attempts
                )
                if attempt < self._max_attempts:
                    backoff = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS)) - 1]
                    self._not_before = self._last_attempt + backoff
                continue
            self._last_attempt = self._monotonic()
            return True
        logger.error("Telegram message dropped after %d attempts", self._max_attempts)
        return False


__all__ = [
    "MAX_SEND_ATTEMPTS",
    "MIN_SEND_INTERVAL_SECONDS",
    "REQUEST_TIMEOUT_SECONDS",
    "TELEGRAM_API_BASE_URL",
    "TELEGRAM_CONFIG_FILENAME",
    "TELEGRAM_MESSAGE_LIMIT",
    "MessageSender",
    "TelegramAPIError",
    "TelegramBotAPI",
    "TelegramChat",
    "TelegramConfigError",
    "TelegramNotifier",
    "chat_from_updates",
    "discover_chat",
    "is_valid_bot_token",
    "load_chat_id",
    "normalize_text",
    "save_chat_id",
    "split_message",
    "telegram_config_file",
    "token_secrets",
    "utf16_length",
]
