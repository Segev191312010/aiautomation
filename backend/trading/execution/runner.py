"""Run the execution engine by hand for one session: ``autotraderctl execution run --confirm``.

NOT wired: no supervisor child, LaunchAgent or schedule runs this module, and
none may until the engine has passed its supervised drill and the owner has
switched the order sender (AGENTS.md). It is the drill's entry point:

* before it takes the TWS order client id it refuses (exit 78) without the
  shadow configuration's paper account, with pending database migrations,
  without an active PAPER grant for exactly this code, or while the PAPER
  bridge runs (its process or a fresh heartbeat). ``LiveExecutor.start()``
  repeats every check once connected;
* it connects with the configuration's order client id, the bridge's: TWS
  refuses a second connection on it, and the interlock keeps the two apart;
* it runs one session through ``LiveExecutor.run()``. SIGTERM and SIGINT stop
  it after the current step (state saved, exit 0). A lost or unreachable TWS
  exits 75 and is never reconnected in-process: a restart, which reconciles
  from the ledger first, is the reviewed recovery path.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import signal
import sqlite3
import sys
import threading
from typing import Protocol

from ibapi.client import EClient
from ibapi.wrapper import EWrapper

from config import cfg
from trading.execution.live_executor import (
    EXIT_REFUSED,
    EXIT_RESTART,
    BrokerPort,
    EventSink,
    ExecutionRefused,
    LiveExecutor,
    format_event_line,
    now_ny,
    stdout_sink,
)
from trading.execution.promotion import EngineAuthority, ExecutionAuthorityError, is_paper_account
from trading.execution.tws_state import TwsMirror
from trading.operations.settings import ShadowSettingsError, load_shadow_runtime_config
from trading.storage.execution_ledger import ExecutionLedger
from trading.storage.migrations import MIGRATIONS


CONNECT_TIMEOUT_S = 30.0  # ibapi 9.81's connect() never returns when TWS accepts TCP but not the handshake


class TwsUnavailable(RuntimeError):
    """TWS could not be reached or did not answer the API handshake: transient (exit 75)."""


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    account: str
    client_id: int
    host: str
    port: int


class Transport(Protocol):
    """The engine's TWS connection: requests go to ``broker``, callbacks land in ``mirror``."""

    broker: BrokerPort
    mirror: TwsMirror

    async def connect(self) -> None: ...
    def disconnect(self) -> None: ...


class EngineWrapper(TwsMirror, EWrapper):
    """The mirror's callbacks, with ibapi's no-op defaults for every callback the engine ignores."""


class IbapiTransport:
    """The official ibapi 9.81 client, with a watchdog on its handshake and its reader thread."""

    def __init__(self, config: RunnerConfig, *, timeout: float = CONNECT_TIMEOUT_S) -> None:
        self.config = config
        self.timeout = timeout
        self.mirror = EngineWrapper(config.client_id)
        self.client = EClient(self.mirror)
        self.broker: BrokerPort = self.client

    async def connect(self) -> None:
        config = self.config
        handshake = threading.Thread(target=self.client.connect, args=(config.host, config.port, config.client_id),
                                     name="ibapi-connect", daemon=True)
        handshake.start()
        await asyncio.to_thread(handshake.join, self.timeout)
        if handshake.is_alive() or not self.client.isConnected():
            self.client.disconnect()
            raise TwsUnavailable(f"no API connection to {config.host}:{config.port} as client {config.client_id}"
                                 f" within {self.timeout:.0f}s")
        threading.Thread(target=self.client.run, name="ibapi-reader", daemon=True).start()

    def disconnect(self) -> None:
        self.client.disconnect()


def pending_migrations(database: Path) -> str | None:
    """Why the database is not at exactly this code's migrations, read-only; None when it is.

    The runner never migrates: an unreviewed schema change must not ride in
    with an order sender (the main line's explicit migrations, review R-7).
    """
    try:
        with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='autotrader_schema_migrations'"
            ).fetchone()
            rows = connection.execute(
                "SELECT version, name, checksum FROM autotrader_schema_migrations"
            ).fetchall() if exists else []
    except sqlite3.Error as exc:
        return f"the database is unreadable ({type(exc).__name__}: {exc})"
    applied = {int(version): (str(name), str(checksum)) for version, name, checksum in rows}
    expected = {migration.version: (migration.name, migration.checksum) for migration in MIGRATIONS}
    unknown = sorted(set(applied) - set(expected))
    if unknown:
        return f"the database has migrations this code does not know: {unknown}"
    changed = sorted(version for version in applied if applied[version] != expected[version])
    if changed:
        return f"applied migrations {changed} differ from this code's (checksum mismatch)"
    missing = sorted(set(expected) - set(applied))
    if missing:
        return f"migrations {missing} are not applied: apply them with a backup first"
    return None


async def preflight(engine: LiveExecutor) -> str | None:
    """Every check that needs no TWS, before the order client id is taken; why it refuses, or None."""
    try:
        await engine.authority.require_start()
        engine.require_reviewed_code()
    except ExecutionAuthorityError as exc:
        return str(exc)
    bridge = await engine.bridge_problem()
    if bridge is not None:
        return f"bridge_running: {bridge} — the engine never runs next to the bridge"
    return None


async def run_session(
    session_date: date,
    *,
    config: RunnerConfig,
    transport: Transport,
    clock: Callable[[], datetime] | None = None,
    sink: EventSink | None = None,
    stop_event: threading.Event | None = None,
    tune: Callable[[LiveExecutor], None] | None = None,
) -> int:
    """One session of the engine on ``transport``; the process exit code (0, 75 or 78)."""
    try:
        engine = LiveExecutor(
            broker=transport.broker, mirror=transport.mirror, ledger=ExecutionLedger(clock=clock),
            authority=EngineAuthority(account=config.account), account=config.account,
            client_id=config.client_id, session_date=session_date, clock=clock, sink=sink, stop_event=stop_event,
        )
    except (ExecutionRefused, ExecutionAuthorityError) as exc:  # not a session day, a non-paper account
        return refuse(f"{exc} — TWS not contacted", sink or stdout_sink, clock)
    if tune is not None:
        tune(engine)
    refusal = await preflight(engine)
    if refusal is not None:
        engine.emit("CRITICAL", f"refusing: {refusal} — TWS not contacted; exit {EXIT_REFUSED}")
        return EXIT_REFUSED
    try:
        await transport.connect()
    except TwsUnavailable as exc:
        engine.emit("CRITICAL", f"TWS not reachable: {exc} — exit {EXIT_RESTART}")
        return EXIT_RESTART
    try:
        return await engine.run()
    finally:
        transport.disconnect()


def refuse(message: str, sink: EventSink = stdout_sink, clock: Callable[[], datetime] | None = None) -> int:
    moment = clock() if clock is not None else now_ny()
    sink(format_event_line("CRITICAL", f"refusing: {message}; exit {EXIT_REFUSED}", moment))
    return EXIT_REFUSED


def runner_config() -> RunnerConfig:
    """The paper account and order client id of the shadow configuration (the bridge's own)."""
    shadow = load_shadow_runtime_config()
    if not is_paper_account(shadow.paper_account_id):
        raise ShadowSettingsError("the configured account is not a DU paper account")
    return RunnerConfig(account=shadow.paper_account_id, client_id=shadow.order_client_id, host=shadow.tws_host,
                        port=shadow.tws_port)


async def _run_until_signalled(session_date: date, config: RunnerConfig, transport: Transport) -> int:
    stop = threading.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    for number in signals:
        loop.add_signal_handler(number, stop.set)  # finishes the step, saves the ledger, exits 0
    try:
        return await run_session(session_date, config=config, transport=transport, stop_event=stop)
    finally:
        for number in signals:
            loop.remove_signal_handler(number)


def main(session_date: date | None, *, confirm: bool, db_path: Path | None = None,
         transport_factory: Callable[[RunnerConfig], Transport] | None = None) -> int:
    """``autotraderctl execution run``: one supervised session of the engine, by hand."""
    if not confirm:
        print("error: run starts the PAPER execution engine (orders for promoted strategies); re-run with"
              " --confirm", file=sys.stderr)
        return 2
    today = now_ny().date()
    day = session_date or today
    if day > today:
        return refuse(f"--date {day} is after today ({today}) in New York")
    original = cfg.DB_PATH
    if db_path is not None:
        cfg.DB_PATH = str(db_path.expanduser())
    try:
        database = Path(cfg.DB_PATH).expanduser()
        if cfg.DB_PATH == ":memory:" or not database.is_file():
            return refuse(f"no autotrader database at {database}")
        problem = pending_migrations(database)
        if problem is not None:
            return refuse(problem)
        try:
            config = runner_config()
        except ShadowSettingsError as exc:
            return refuse(str(exc))
        transport = (transport_factory or IbapiTransport)(config)
        return asyncio.run(_run_until_signalled(day, config, transport))
    finally:
        cfg.DB_PATH = original


__all__ = [
    "CONNECT_TIMEOUT_S",
    "EngineWrapper",
    "IbapiTransport",
    "RunnerConfig",
    "Transport",
    "TwsUnavailable",
    "main",
    "pending_migrations",
    "preflight",
    "run_session",
    "runner_config",
]
