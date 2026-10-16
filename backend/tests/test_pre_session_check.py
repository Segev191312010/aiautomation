"""T-4b: the pre-session real-data check (owner decision 2026-10-16).

One daily-history request like the screener's qualification, at 05:55 and
07:40 ET on session days, on the assistant's own client-21 connection. The
tests drive the real ``TWSPaperActor`` (with the fake TWS client of
test_tws_actor) through the real ``TWSDataSession`` and, for the schedule,
the real ``OvernightWatch`` loop; nothing touches a socket."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import logging
from typing import Any

from tests.test_overnight_watch import MONDAY, SATURDAY, Port, Time, drive, et, make_watch
from tests.test_tws_actor import FakeTWSClient
from trading.market.tws_actor import DAILY_HISTORY_DURATION, TWSPaperActor
from trading.operations.pre_session_check import (
    LOGGED_IN_ELSEWHERE_TEXT,
    NO_PRICES_TEXT,
    NOT_RUN_TEXT,
    PRE_SESSION_CHECK_SYMBOLS,
    PreSessionCheck,
    actor_request_timeout,
    universe_check_contract,
)
from trading.operations.telegram_tools import TELEGRAM_TWS_CLIENT_ID, TWSDataSession

ACCOUNT = "DU123456"
AAPL = ("AAPL", 265598)
LEAD = et(MONDAY, 5, 55)
CHECK_0555 = "05:55"


class Client(FakeTWSClient):
    """The fake TWS client, with what the Gateway says to a daily-history request."""

    def __init__(self, callbacks: Any, *, history: str = "bars", status: tuple[int, ...] = ()) -> None:
        super().__init__(callbacks)
        self.history = history
        self.status = status

    def connect(self, host: str, port: int, client_id: int) -> None:
        super().connect(host, port, client_id)
        for code in (2104, 2106, 2158, *self.status):  # the farms' "OK" on every connect, then any broken
            self.callbacks.error(-1, code, f"farm status {code}")

    def reqHistoricalData(self, *args: object) -> None:
        if self.history == "bars":
            super().reqHistoricalData(*args)
            return
        self.history_calls.append(args)
        request_id = int(args[0])
        if self.history == "competing":
            self.callbacks.error(request_id, 10197, "No market data during competing live session")
        elif self.history == "empty":
            self.callbacks.historicalDataEnd(request_id)
        # "silent": nothing ever comes back (GW-HMDS-1016): the actor's timeout


class Gateway:
    """The session's actor factory: the real actor, one fake client per connection."""

    def __init__(self, *, request_timeout: float | None = None, **client: Any) -> None:
        self.request_timeout = request_timeout
        self.client = client
        self.configs: list[Any] = []
        self.clients: list[Client] = []
        self.actors: list[TWSPaperActor] = []

    def __call__(self, config: Any) -> TWSPaperActor:
        self.configs.append(config)
        if self.request_timeout is not None:
            config = replace(config, request_timeout_seconds=self.request_timeout)

        def factory(callbacks: Any) -> Client:
            made = Client(callbacks, **self.client)
            self.clients.append(made)
            return made

        actor = TWSPaperActor(config, client_factory=factory)
        self.actors.append(actor)
        return actor

    @property
    def requests(self) -> list[tuple[object, ...]]:
        return [call for client in self.clients for call in client.history_calls]


def session_for(gateway: Gateway, clock: list[float] | None = None) -> TWSDataSession:
    mono = clock if clock is not None else [1000.0]
    return TWSDataSession(account=ACCOUNT, factory=gateway, monotonic=lambda: mono[0])


async def contract_aapl() -> tuple[str, int] | None:
    return AAPL


async def port_open() -> bool:
    return True


def check_for(session: TWSDataSession | None, **kwargs: Any) -> PreSessionCheck:
    kwargs.setdefault("find_contract", contract_aapl)
    kwargs.setdefault("probe", port_open)
    return PreSessionCheck(session=session, **kwargs)


# --------------------------------------------------------------------------
# One real request, one verdict
# --------------------------------------------------------------------------


async def test_bars_returned_is_healthy_and_silent_after_one_screener_style_request(caplog):
    gateway = Gateway()
    session = session_for(gateway)
    caplog.set_level(logging.INFO, logger="autotrader.shadow.telegram")

    assert await check_for(session)(LEAD, LEAD) is None

    # Exactly the screener's qualification request: one, for the chosen conId.
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert getattr(request[1], "conId") == AAPL[1]
    assert request[2:] == ("", DAILY_HISTORY_DURATION, "1 day", "TRADES", 1, 1, False, [])
    assert DAILY_HISTORY_DURATION == "2 M"
    # On the assistant's own data client, with the actor's own 20 s request timeout.
    assert [config.client_id for config in gateway.configs] == [TELEGRAM_TWS_CLIENT_ID] == [21]
    assert gateway.configs[0].request_timeout_seconds == actor_request_timeout() == 20.0
    assert "pre-session data check 05:55 ET: 2 daily bars for AAPL (conId 265598)" in caplog.text


async def test_a_request_that_times_out_pages_not_returning_prices():
    gateway = Gateway(history="silent", request_timeout=0.05)

    page = await check_for(session_for(gateway))(LEAD, LEAD)

    assert page == NO_PRICES_TEXT.format(check=CHECK_0555)
    assert page == (
        "⛔ IB Gateway is not returning prices (05:55 ET check) — 1) log out of the IBKR app and"
        " website, 2) restart IB Gateway: File → Exit, reopen, log in"
    )
    assert len(gateway.requests) == 1


async def test_a_competing_session_10197_pages_log_out_elsewhere():
    gateway = Gateway(history="competing")

    page = await check_for(session_for(gateway))(LEAD, LEAD)

    assert page == LOGGED_IN_ELSEWHERE_TEXT.format(check=CHECK_0555)
    assert page == "⛔ IBKR is logged in elsewhere — log out of IBKR on your phone and the website (05:55 ET check)"


async def test_2110_with_no_bars_pages_and_records_the_code():
    gateway = Gateway(history="empty", status=(2110, 2105))
    check = check_for(session_for(gateway))

    result = await check.run()

    assert result.verdict == "no_prices" and result.bars == 0
    assert result.status_codes == (2110, 2105)  # never the farms' 2104/2106/2158
    assert await check(LEAD, LEAD) == NO_PRICES_TEXT.format(check=CHECK_0555)


async def test_status_codes_alone_never_pass_the_check():
    """The mutation that reads status codes instead of requesting data must fail
    here: every farm "OK", no broken code, and no bars -> it pages."""
    gateway = Gateway(history="empty")
    check = check_for(session_for(gateway))

    result = await check.run()

    assert result.status_codes == () and result.verdict == "no_prices"
    assert len(gateway.requests) == 1
    assert await check(LEAD, LEAD) == NO_PRICES_TEXT.format(check=CHECK_0555)


async def test_the_shared_connection_keeps_its_default_listeners_and_idles_out():
    mono = [1000.0]
    gateway = Gateway(status=(2110,))
    session = session_for(gateway, mono)

    assert await check_for(session)(LEAD, LEAD) is None

    actor = gateway.actors[0]
    # Both listeners were the check's alone: removed, the actor is as /quote knows it.
    assert actor._status_listener is None and actor._competing_session_listener is None
    assert actor.connected
    mono[0] += 59.0
    await session.close_if_idle()
    assert actor.connected
    mono[0] += 1.0
    await session.close_if_idle()
    assert not actor.connected and gateway.clients[0].disconnected


async def test_a_connection_refused_after_the_port_check_is_the_port_watchs():
    class Refused(Client):
        def connect(self, host: str, port: int, client_id: int) -> None:
            raise ConnectionRefusedError(61, "Connection refused")

    class RefusingGateway(Gateway):
        def __call__(self, config: Any) -> TWSPaperActor:
            self.configs.append(config)
            return TWSPaperActor(config, client_factory=lambda callbacks: Refused(callbacks))

    async def closed() -> bool:
        return False

    check = check_for(session_for(RefusingGateway()), probe=closed)
    assert (await check.run()).verdict == "port_closed"
    assert await check(LEAD, LEAD) is None
    # The port still answers, the API does not: not returning prices.
    assert await check_for(session_for(RefusingGateway()))(LEAD, LEAD) == NO_PRICES_TEXT.format(check=CHECK_0555)


async def test_a_check_that_cannot_run_says_so():
    async def nothing() -> tuple[str, int] | None:
        return None

    async def broken() -> tuple[str, int] | None:
        raise OSError("disk")

    assert await check_for(None, unavailable="shadow configuration is unavailable")(LEAD, LEAD) == (
        NOT_RUN_TEXT.format(check=CHECK_0555, reason="shadow configuration is unavailable")
    )
    session = session_for(Gateway())
    assert await check_for(session, find_contract=nothing)(LEAD, LEAD) == (
        "⚠️ Pre-session data check could not run (05:55 ET check): no resolved contract in the universe"
    )
    assert await check_for(session, find_contract=broken)(LEAD, LEAD) == (
        "⚠️ Pre-session data check could not run (05:55 ET check): the universe could not be read (OSError)"
    )


async def test_the_contract_is_the_first_pinned_symbol_the_universe_resolved(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db
    from tests.test_screener import universe_row
    from trading.market.universe import replace_universe_source

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "universe.db"))
    await init_db()
    assert await universe_check_contract() is None

    await replace_universe_source("watchlist", [universe_row("ZZZ", 11), universe_row("ABC", 12)])
    assert await universe_check_contract() == ("ABC", 12)  # no pinned name: the first row
    await replace_universe_source(
        "watchlist",
        [
            universe_row("ZZZ", 11),
            universe_row("MSFT", 272093),
            universe_row("AAPL", 265598),
            universe_row("NVDA", None, status="unresolved"),
        ],
    )
    assert PRE_SESSION_CHECK_SYMBOLS[:2] == ("AAPL", "MSFT")
    assert await universe_check_contract() == AAPL


# --------------------------------------------------------------------------
# The schedule: the overnight watch's leads, session days, the port first
# --------------------------------------------------------------------------


async def test_the_0740_check_after_a_0555_failure_pages_again(tmp_path):
    gateway = Gateway(history="silent", request_timeout=0.01)
    clock = Time(et(MONDAY, 5, 50), et(MONDAY, 7, 45))
    watch, _ = make_watch(tmp_path, clock, Port(clock), lead_check=check_for(session_for(gateway)))

    sent = await drive(watch)

    assert sent == [NO_PRICES_TEXT.format(check="05:55"), NO_PRICES_TEXT.format(check="07:40")]
    assert len(gateway.requests) == 2


async def test_a_healthy_morning_sends_nothing_and_makes_two_requests(tmp_path):
    gateway = Gateway()
    clock = Time(et(MONDAY, 5, 0), et(MONDAY, 8, 0))
    watch, _ = make_watch(tmp_path, clock, Port(clock), lead_check=check_for(session_for(gateway)))

    assert await drive(watch) == []
    assert len(gateway.requests) == 2


async def test_a_weekend_makes_no_check_and_no_connection(tmp_path):
    gateway = Gateway()
    clock = Time(et(SATURDAY, 5, 0), et(SATURDAY, 9, 0))
    watch, _ = make_watch(tmp_path, clock, Port(clock), lead_check=check_for(session_for(gateway)))

    assert await drive(watch) == []
    assert gateway.configs == []


async def test_a_closed_port_at_the_lead_is_paged_once_by_the_port_watch_and_not_checked(tmp_path):
    gateway = Gateway()
    clock = Time(et(MONDAY, 5, 50), et(MONDAY, 5, 58))
    port = Port(clock, [(et(MONDAY, 5, 45), et(MONDAY, 9, 0))])
    watch, _ = make_watch(tmp_path, clock, port, lead_check=check_for(session_for(gateway)))

    sent = await drive(watch)

    assert len(sent) == 1 and sent[0].startswith("⛔ IB Gateway closed since 05:50 ET")
    assert gateway.configs == []


async def test_end_to_end_the_composition_root_runs_the_check_at_the_lead(tmp_path, monkeypatch):
    """run_telegram_assistant -> _serve: the watch's lead hook is the real check on
    the assistant's own TWSDataSession (the environment's actor factory, client 21)."""
    import httpx
    import json

    from tests.test_screener import runtime_config
    from tests.test_telegram_bot import OWNER, env, fence_database
    from trading.operations import settings, telegram_bot as bot
    from trading.operations.keychain import KeychainReadError
    from trading.operations.notifier import save_chat_id, telegram_config_file

    await fence_database(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "load_shadow_runtime_config", lambda **_: runtime_config())
    monkeypatch.setattr(
        "trading.operations.pre_session_check.universe_check_contract", contract_aapl, raising=True
    )
    gateway = Gateway(history="silent", request_timeout=0.05)
    now = {"at": et(MONDAY, 5, 54)}
    sent: list[str] = []
    page = NO_PRICES_TEXT.format(check="05:55")

    async def sleep(seconds: float) -> None:
        now["at"] += timedelta(seconds=seconds)
        await asyncio.sleep(0)

    async def handle(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        if method == "sendMessage":
            sent.append(payload["text"])
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})
        await asyncio.sleep(0.01)
        if page in sent:
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        return httpx.Response(200, json={"ok": True, "result": []})

    def no_key() -> str:
        raise KeychainReadError("no key in this test")

    environment = env(
        tmp_path,
        transport=httpx.MockTransport(handle),
        read_anthropic_key=no_key,
        clock=lambda: now["at"],
        sleep=sleep,
        gateway_probe=port_open,
        tws_factory=gateway,
    )
    save_chat_id(telegram_config_file(environment.paths), OWNER)

    assert await asyncio.wait_for(bot.run_telegram_assistant(env=environment), 20) == bot.EXIT_CONFIG
    assert page in sent
    assert [config.client_id for config in gateway.configs][:1] == [TELEGRAM_TWS_CLIENT_ID]
    assert len(gateway.requests) == 1
