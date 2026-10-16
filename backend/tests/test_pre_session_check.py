"""T-4b: the pre-session real-data check (owner decision 2026-10-16).

One daily-history request like the screener's qualification, at 05:55 and
07:40 ET on session days, on the assistant's own client-21 connection. The
tests drive the real ``TWSPaperActor`` (with the fake TWS client of
test_tws_actor) through the real ``TWSDataSession`` and, for the schedule,
the real ``OvernightWatch`` loop; nothing touches a socket."""
from __future__ import annotations

import asyncio
from dataclasses import replace
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
    RETRY_AFTER_SECONDS,
    PreSessionCheck,
    actor_request_timeout,
    universe_check_contracts,
)
from trading.operations.telegram_tools import TELEGRAM_TWS_CLIENT_ID, TWSDataSession

ACCOUNT = "DU1234567"
AAPL = ("AAPL", 265598)
MSFT = ("MSFT", 272093)
LEAD = et(MONDAY, 5, 55)
CHECK_0555 = "05:55"


class Client(FakeTWSClient):
    """The fake TWS client, with what the Gateway says to a daily-history request
    (``history``: one answer for every request, or a list of answers in order,
    shared by every connection)."""

    def __init__(self, callbacks: Any, *, history: str | list[str] = "bars", status: tuple[int, ...] = ()) -> None:
        super().__init__(callbacks)
        self.script = history
        self.status = status

    @property
    def history(self) -> str:
        return self.script[0] if isinstance(self.script, list) else self.script

    def connect(self, host: str, port: int, client_id: int) -> None:
        super().connect(host, port, client_id)
        for code in (2104, 2106, 2158, *self.status):  # the farms' "OK" on every connect, then any broken
            self.callbacks.error(-1, code, f"farm status {code}")

    def reqManagedAccts(self) -> None:
        self.callbacks.managedAccounts(ACCOUNT)

    def reqHistoricalData(self, *args: object) -> None:
        answer = self.script.pop(0) if isinstance(self.script, list) else self.script
        if answer == "bars":
            super().reqHistoricalData(*args)
            return
        self.history_calls.append(args)
        request_id = int(args[0])
        if answer == "competing":
            self.callbacks.error(request_id, 10197, "No market data during competing live session")
        elif answer == "empty":
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


async def contracts_aapl_msft() -> tuple[tuple[str, int], ...]:
    return (AAPL, MSFT)


async def contract_aapl_only() -> tuple[tuple[str, int], ...]:
    return (AAPL,)


async def port_open() -> bool:
    return True


class Waits:
    """The check's sleeper: records each wait, returns at once."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)
        await asyncio.sleep(0)


def check_for(session: TWSDataSession | None, **kwargs: Any) -> PreSessionCheck:
    kwargs.setdefault("find_contracts", contracts_aapl_msft)
    kwargs.setdefault("probe", port_open)
    kwargs.setdefault("sleep", Waits())
    return PreSessionCheck(session=session, **kwargs)


def con_ids(gateway: "Gateway") -> list[int]:
    return [int(getattr(request[1], "conId")) for request in gateway.requests]


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
    # Confirmed once, 30 s later, on the fallback contract (N15b L-4).
    assert con_ids(gateway) == [AAPL[1], MSFT[1]]


async def test_one_transient_timeout_is_confirmed_on_the_fallback_and_never_pages(caplog):
    """N15b L-4: a slow first request of the day is not a page. The hook waits
    30 s (the lock released), asks once more on MSFT, and the bars come back."""
    gateway = Gateway(history=["silent", "bars"], request_timeout=0.05)
    waits = Waits()
    caplog.set_level(logging.INFO, logger="autotrader.shadow.telegram")

    assert await check_for(session_for(gateway), sleep=waits)(LEAD, LEAD) is None

    assert waits.seconds == [RETRY_AFTER_SECONDS] == [30.0]
    assert con_ids(gateway) == [AAPL[1], MSFT[1]]
    assert "first request failed" in caplog.text and "one more in 30 s on the fallback contract" in caplog.text
    assert "pre-session data check 05:55 ET: 2 daily bars for MSFT (conId 272093)" in caplog.text


async def test_a_real_refusal_still_pages_within_the_same_lead():
    """Both requests refused: the page, once, well inside the lead's grace (two
    bounded requests and one 30 s wait; LEAD_GRACE is 5 min)."""
    from trading.operations.overnight_watch import LEAD_GRACE
    from trading.operations.pre_session_check import DEADLINE_MARGIN_SECONDS

    for history in (["silent", "silent"], ["empty", "empty"], ["silent", "empty"]):
        gateway = Gateway(history=history, request_timeout=0.05)
        waits = Waits()
        assert await check_for(session_for(gateway), sleep=waits)(LEAD, LEAD) == NO_PRICES_TEXT.format(
            check=CHECK_0555
        ), history
        assert waits.seconds == [30.0] and con_ids(gateway) == [AAPL[1], MSFT[1]], history
    worst = 2 * (actor_request_timeout() + DEADLINE_MARGIN_SECONDS) + RETRY_AFTER_SECONDS
    assert worst == 80.0 and worst < LEAD_GRACE.total_seconds()
    # A competing login is a definite answer: paged at once, no wait, no second request.
    gateway = Gateway(history=["competing", "bars"])
    waits = Waits()
    assert await check_for(session_for(gateway), sleep=waits)(LEAD, LEAD) == LOGGED_IN_ELSEWHERE_TEXT.format(
        check=CHECK_0555
    )
    assert waits.seconds == [] and con_ids(gateway) == [AAPL[1]]


async def test_the_check_holds_the_client_21_lock_for_its_whole_request():
    """N15a LOW-2 (mutation M7): while the check's request runs, another user of
    the shared session (/quote, a manual order's facts) waits for the lock, and
    then finds the actor's default listeners, never the check's."""
    gateway = Gateway(history="silent", request_timeout=0.3)
    session = session_for(gateway)
    loop = asyncio.get_running_loop()
    check = asyncio.create_task(check_for(session).run())
    async with asyncio.timeout(5):  # a broken check fails here, never hangs
        while not gateway.requests:
            await asyncio.sleep(0.005)
        asked_at = loop.time()
        seen: list[tuple[float, Any, Any]] = []

        async with session.use() as actor:
            seen.append((loop.time() - asked_at, actor._status_listener, actor._competing_session_listener))
            assert check.done(), "another user got the connection while the check's request ran"

        result = await check
    assert result.verdict == "no_prices"
    [(waited, status, competing)] = seen
    assert waited >= 0.25
    assert status is None and competing is None


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
    assert len(gateway.requests) == 3  # the hook confirms once before it pages


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
    async def nothing() -> tuple[tuple[str, int], ...]:
        return ()

    async def broken() -> tuple[tuple[str, int], ...]:
        raise OSError("disk")

    assert await check_for(None, unavailable="shadow configuration is unavailable")(LEAD, LEAD) == (
        NOT_RUN_TEXT.format(check=CHECK_0555, reason="shadow configuration is unavailable")
    )
    session = session_for(Gateway())
    assert await check_for(session, find_contracts=nothing)(LEAD, LEAD) == (
        "⚠️ Pre-session data check could not run (05:55 ET check): no resolved contract in the universe"
    )
    assert await check_for(session, find_contracts=broken)(LEAD, LEAD) == (
        "⚠️ Pre-session data check could not run (05:55 ET check): the universe could not be read (OSError)"
    )


async def test_the_contract_is_the_first_pinned_symbol_the_universe_resolved(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db
    from tests.test_screener import universe_row
    from trading.market.universe import replace_universe_source

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "universe.db"))
    await init_db()
    assert await universe_check_contracts() == ()

    await replace_universe_source("watchlist", [universe_row("ZZZ", 11), universe_row("ABC", 12)])
    # No pinned name: the first rows by source and symbol.
    assert await universe_check_contracts() == (("ABC", 12), ("ZZZ", 11))
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
    # The check's contract, then its fallback: the pinned order, never ZZZ.
    assert await universe_check_contracts() == (AAPL, MSFT)
    assert await universe_check_contracts(limit=1) == (AAPL,)


# --------------------------------------------------------------------------
# The schedule: the overnight watch's leads, session days, the port first
# --------------------------------------------------------------------------


async def test_the_0740_check_after_a_0555_failure_pages_again(tmp_path):
    gateway = Gateway(history="silent", request_timeout=0.01)
    clock = Time(et(MONDAY, 5, 50), et(MONDAY, 7, 45))
    watch, _ = make_watch(tmp_path, clock, Port(clock), lead_check=check_for(session_for(gateway)))

    sent = await drive(watch)

    assert sent == [NO_PRICES_TEXT.format(check="05:55"), NO_PRICES_TEXT.format(check="07:40")]
    assert con_ids(gateway) == [AAPL[1], MSFT[1]] * 2


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

    assert len(sent) == 1 and sent[0].startswith("⛔ IB Gateway closed since before 05:50 ET")
    assert gateway.configs == []


async def test_end_to_end_the_composition_root_runs_the_check_at_the_lead(tmp_path, monkeypatch):
    """run_telegram_assistant -> _serve: the watch's lead hook is the real check on
    the assistant's own TWSDataSession (the environment's actor factory, client 21)."""
    import httpx
    import json

    from tests.test_screener import runtime_config
    from tests.test_telegram_bot import OWNER, CeilingClock, env, fence_database, run_bounded
    from trading.operations import settings, telegram_bot as bot
    from trading.operations.keychain import KeychainReadError
    from trading.operations.notifier import save_chat_id, telegram_config_file

    await fence_database(tmp_path, monkeypatch)
    monkeypatch.setattr(
        settings, "load_shadow_runtime_config", lambda **_: runtime_config(paper_account_id=ACCOUNT)
    )
    monkeypatch.setattr(
        "trading.operations.pre_session_check.universe_check_contracts", contract_aapl_only, raising=True
    )
    gateway = Gateway(history="silent", request_timeout=0.05)
    # Never past 06:00 ET: one lead (05:55) and nothing after it.
    clock = CeilingClock(et(MONDAY, 5, 54), ceiling=et(MONDAY, 6, 0))
    sent: list[str] = []
    page = NO_PRICES_TEXT.format(check="05:55")

    async def handle(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        if method == "sendMessage":
            sent.append(payload["text"])
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})
        await asyncio.sleep(0)
        if page in sent or clock.stalled():
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
        gateway_probe=port_open,
        tws_factory=gateway,
    )
    save_chat_id(telegram_config_file(environment.paths), OWNER)

    assert await run_bounded(bot.run_telegram_assistant(env=environment)) == bot.EXIT_CONFIG
    assert sent == [page], f"expected the one 05:55 page; sent {sent}, clock {clock.now}"
    assert [config.client_id for config in gateway.configs][:1] == [TELEGRAM_TWS_CLIENT_ID]
    # Confirmed once on the environment's own sleeper (no fallback resolved: AAPL again).
    assert con_ids(gateway) == [AAPL[1], AAPL[1]]


async def test_a_stop_that_lands_as_the_bars_return_still_cancels_the_check():
    """3.11's wait_for (gh-86296): the request ran in a task of its own, and a
    stop landing as it returned was dropped. The check must end cancelled."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    holder: dict = {}

    class Actor:
        connected = True

        async def daily_trade_bars(self, con_id: int) -> Any:
            holder["task"].cancel()
            return SimpleNamespace(bars=("bar",), competing_session=False, error=None)

    class Session:
        @asynccontextmanager
        async def use(self, **listeners: Any):
            yield Actor()

    task = asyncio.create_task(check_for(Session())(LEAD, LEAD))
    holder["task"] = task
    await asyncio.wait({task})

    assert task.cancelled(), f"the stop was lost: the check returned {task.result()!r}"


async def test_a_stop_that_lands_as_the_connect_returns_still_cancels_the_session_user():
    """The shared client-21 session's connect: the same 3.11 wait_for race."""
    holder: dict = {}

    class Actor:
        connected = False
        managed_accounts = frozenset({ACCOUNT})

        async def connect(self) -> None:
            holder["task"].cancel()
            self.connected = True

        async def disconnect(self) -> None:
            self.connected = False

    session = TWSDataSession(account=ACCOUNT, factory=lambda config: Actor())

    async def user() -> str:
        async with session.use():
            return "used"

    task = asyncio.create_task(user())
    holder["task"] = task
    await asyncio.wait({task})

    assert task.cancelled(), f"the stop was lost: the session user got {task.result()!r}"
