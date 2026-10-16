"""The pre-session real-data check (T-4b).

Owner decision, 2026-10-16: "Make the check request real data, not just read
status codes … one request of the kind the screener makes at 15:00 [Israel].
At 12:55 Israel it's 05:55 in New York and live quotes may be thin, so a
historical-bar request is a safer probe at that hour." Live 2026-10-16
(GW-HMDS-1016) the Gateway's port was open and its status said little while
IBKR refused every price request from 08:00 ET.

At each pre-session lead (05:55 and 07:40 ET on session days; the overnight
watch calls it, and only when the port check found the port open) the
Telegram child makes ONE daily-history request exactly like the screener's
qualification: ``TWSPaperActor.daily_trade_bars`` (DAILY_HISTORY_DURATION
"2 M" of "1 day" TRADES bars, regular hours), with the actor's own request
timeout (20 s), for one liquid contract of the configured universe. It uses
the assistant's own shared TWS data connection (client 21,
``telegram_tools.TWSDataSession``): the session's lock serializes it with
manual orders, /status and /quote, and the connection idles out after 60 s
as before. While the request runs it records TWS 2103/2105/2157/2110 (the
actor's opt-in status listener) and any competing-session error
(``ibkr_boundary.is_competing_session_error``: 10197, or 162/165 with
"different IP address"); both listeners are removed afterwards.

  * bars returned: healthy. Nothing is sent; one INFO line;
  * a competing session: ``LOGGED_IN_ELSEWHERE_TEXT``, at once;
  * a timeout, an error, no bars (2110 or not): confirmed once before it pages
    (N15b L-4): ``RETRY_AFTER_SECONDS`` later ONE more request, on the fallback
    contract (the next pinned symbol the universe resolved, MSFT after AAPL;
    the same contract when there is none), so a slow first request of the day
    or one symbol's missing bars is not a page. A second failure pages
    ``NO_PRICES_TEXT``, the fix of GW-HMDS-1016 in its order (log out of the
    app and website, then restart IB Gateway), still inside the lead (about
    75 s after it at worst). The lock is not held across the wait;
  * the port closed (re-checked when the connection itself fails): nothing,
    the port watch pages it, never twice;
  * the check cannot run (no IBKR data configured for the assistant, no
    resolved contract in the universe, the universe unreadable):
    ``NOT_RUN_TEXT``, so a check that cannot see is never silent.

The contracts: the first two of ``PRE_SESSION_CHECK_SYMBOLS`` the stored
universe has resolved (the rows the screener qualifies, by conId), in that
order, else the universe's first resolved rows by source and symbol (any
listed stock has two months of daily bars): the first is the check's, the
second its fallback. Nothing here can place, change or cancel an order.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
import logging
from typing import Any, Literal

from trading.market.tws_actor import (
    TWSActorConfig,
    TWSCompetingSessionError,
    TWSConnectionError,
    TWSRequestError,
)
from trading.scheduler.sessions import MARKET_TIMEZONE

logger = logging.getLogger("autotrader.shadow.telegram")

# Liquid names, most liquid first; the first the universe has resolved is the
# probe's contract (pinned so the check is the same request every morning).
PRE_SESSION_CHECK_SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL")
# The codes the check records while its request runs (the actor's own set).
RECORDED_STATUS_CODES = frozenset({2103, 2105, 2110, 2157})
# Above the actor's own request timeout: only a hung actor ever reaches it.
DEADLINE_MARGIN_SECONDS = 5.0
# A failed first request is confirmed once, this much later, on the fallback contract.
RETRY_AFTER_SECONDS = 30.0
# The check's contract and its fallback.
CHECK_CONTRACTS = 2

NO_PRICES_TEXT = (
    "⛔ IB Gateway is not returning prices ({check} ET check) — 1) log out of the IBKR app and"
    " website, 2) restart IB Gateway: File → Exit, reopen, log in"
)
LOGGED_IN_ELSEWHERE_TEXT = (
    "⛔ IBKR is logged in elsewhere — log out of IBKR on your phone and the website ({check} ET check)"
)
NOT_RUN_TEXT = "⚠️ Pre-session data check could not run ({check} ET check): {reason}"

Verdict = Literal["healthy", "no_prices", "logged_in_elsewhere", "port_closed", "not_run"]
# ``(symbol, conId)`` pairs, the check's contract first and its fallback next.
ContractFinder = Callable[[], Awaitable[Sequence[tuple[str, int]]]]
PortProbe = Callable[[], Awaitable[bool]]
Sleeper = Callable[[float], Awaitable[None]]


def actor_request_timeout() -> float:
    """``TWSActorConfig``'s own request timeout (20 s): the check's, unchanged."""
    return float(next(item.default for item in fields(TWSActorConfig) if item.name == "request_timeout_seconds"))


@dataclass(frozen=True, slots=True)
class CheckResult:
    verdict: Verdict
    detail: str
    symbol: str = ""
    con_id: int = 0
    bars: int = 0
    status_codes: tuple[int, ...] = ()


async def universe_check_contracts(limit: int = CHECK_CONTRACTS) -> tuple[tuple[str, int], ...]:
    """``(symbol, conId)`` pairs: the pinned symbols the universe resolved, in pin
    order, then its other resolved rows by source and symbol; at most ``limit``."""
    from db.core import get_db

    order = " ".join(f"WHEN ? THEN {rank}" for rank in range(len(PRE_SESSION_CHECK_SYMBOLS)))
    async with get_db() as db:
        async with db.execute(
            f"""SELECT ibkr_symbol, con_id FROM autotrader_universe
                WHERE status = 'resolved' AND con_id IS NOT NULL
                ORDER BY CASE upper(ibkr_symbol) {order} ELSE {len(PRE_SESSION_CHECK_SYMBOLS)} END,
                         source, symbol
                LIMIT ?""",
            (*PRE_SESSION_CHECK_SYMBOLS, limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return tuple((str(row[0]), int(row[1])) for row in rows)


async def _stored_universe_contracts() -> tuple[tuple[str, int], ...]:
    # Resolved at call time, so a test can replace the module's finder.
    return await universe_check_contracts()


async def _default_probe() -> bool:
    from trading.operations import telegram_tools

    return await telegram_tools.gateway_port_open()


class _Seen:
    """What TWS said during the request (called on the API's reader thread)."""

    def __init__(self) -> None:
        self.codes: list[int] = []
        self.competing: list[str] = []

    def status(self, code: int, message: str) -> None:
        if code in RECORDED_STATUS_CODES:
            self.codes.append(code)

    def competing_report(self, report: str) -> None:
        self.competing.append(report)


class PreSessionCheck:
    """The lead hook of ``overnight_watch.OvernightWatch``: a real request, confirmed once, one verdict."""

    def __init__(
        self,
        *,
        session: Any | None,
        unavailable: str = "",
        find_contracts: ContractFinder | None = None,
        probe: PortProbe | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._session = session
        self._unavailable = unavailable
        self._find_contracts = find_contracts or _stored_universe_contracts
        self._probe = probe or _default_probe
        self._sleep = sleep
        self._deadline = actor_request_timeout() + DEADLINE_MARGIN_SECONDS

    async def __call__(self, now: datetime, lead_at: datetime) -> str | None:
        check = lead_at.astimezone(MARKET_TIMEZONE).strftime("%H:%M")
        result = await self.run()
        if result.verdict == "no_prices":
            # One slow request or one symbol's missing bars is not a page (N15b L-4).
            logger.warning(
                "pre-session data check %s ET: first request failed (%s) for %s;"
                " one more in %d s on the fallback contract",
                check, result.detail, _subject(result), round(RETRY_AFTER_SECONDS),
            )
            await self._sleep(RETRY_AFTER_SECONDS)
            result = await self.run(fallback=True)
        codes = ", ".join(str(code) for code in result.status_codes) or "none"
        if result.verdict == "healthy":
            logger.info(
                "pre-session data check %s ET: %d daily bars for %s; TWS status codes: %s",
                check, result.bars, _subject(result), codes,
            )
            return None
        logger.warning(
            "pre-session data check %s ET: %s (%s) for %s; TWS status codes: %s",
            check, result.verdict, result.detail, _subject(result), codes,
        )
        if result.verdict == "port_closed":
            return None  # the port watch's page, never a second one
        if result.verdict == "logged_in_elsewhere":
            return LOGGED_IN_ELSEWHERE_TEXT.format(check=check)
        if result.verdict == "not_run":
            return NOT_RUN_TEXT.format(check=check, reason=result.detail)
        return NO_PRICES_TEXT.format(check=check)

    async def run(self, *, fallback: bool = False) -> CheckResult:
        """One request: on the check's contract, or with ``fallback`` on the next one
        (the check's own when the universe resolved only one)."""
        if self._session is None:
            return CheckResult("not_run", self._unavailable or "IBKR data is not configured for the assistant")
        try:
            contracts = tuple(await self._find_contracts())
        except Exception as exc:  # noqa: BLE001 - a check that cannot see says so
            return CheckResult("not_run", f"the universe could not be read ({type(exc).__name__})")
        if not contracts:
            return CheckResult("not_run", "no resolved contract in the universe")
        symbol, con_id = contracts[1] if fallback and len(contracts) > 1 else contracts[0]
        seen = _Seen()

        def result(verdict: Verdict, detail: str, bars: int = 0) -> CheckResult:
            return CheckResult(verdict, detail, symbol, con_id, bars, tuple(seen.codes))

        try:
            async with self._session.use(
                status_listener=seen.status, competing_listener=seen.competing_report
            ) as actor:
                # Not wait_for: on 3.11 it runs the request in a task of its own and
                # can drop a stop that lands as the request returns (gh-86296).
                async with asyncio.timeout(self._deadline):
                    batch = await actor.daily_trade_bars(con_id)
        except asyncio.CancelledError:
            raise
        except TWSCompetingSessionError as exc:
            return result("logged_in_elsewhere", str(exc)[:200])
        except (TWSConnectionError, asyncio.TimeoutError) as exc:
            # The connection itself failed: a port that closed since the check is the watch's.
            if not await self._port_open():
                return result("port_closed", type(exc).__name__)
            return result("no_prices", str(exc)[:200] or type(exc).__name__)
        except TWSRequestError as exc:
            return result("no_prices", str(exc)[:200])
        except Exception as exc:  # noqa: BLE001 - any failure is "not returning prices"
            return result("no_prices", type(exc).__name__)
        if batch.competing_session or seen.competing:
            return result("logged_in_elsewhere", str(batch.error or seen.competing[0])[:200])
        if batch.bars:
            return result("healthy", "bars returned", len(batch.bars))
        return result("no_prices", str(batch.error or "no bars returned")[:200])

    async def _port_open(self) -> bool:
        try:
            return bool(await self._probe())
        except Exception:  # noqa: BLE001 - unknown: report what the request saw
            return True


def _subject(result: CheckResult) -> str:
    return f"{result.symbol} (conId {result.con_id})" if result.symbol else "no contract"


__all__ = [
    "LOGGED_IN_ELSEWHERE_TEXT",
    "NOT_RUN_TEXT",
    "NO_PRICES_TEXT",
    "PRE_SESSION_CHECK_SYMBOLS",
    "RETRY_AFTER_SECONDS",
    "CheckResult",
    "PreSessionCheck",
    "actor_request_timeout",
    "universe_check_contracts",
]
