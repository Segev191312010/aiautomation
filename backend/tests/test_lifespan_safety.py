"""Runtime containment tests for process ownership and simulation mode."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import config
import main
import startup
from routers import status


@pytest.mark.anyio
async def test_lifespan_holds_execution_lock_through_inner_lifecycle(
    monkeypatch,
    tmp_path,
    anyio_backend,
):
    events: list[str] = []
    db_path = str(tmp_path / "runtime.db")
    lock_path = str(tmp_path / "runtime.lock")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)

    def acquire(*, db_path: str) -> str:
        events.append(f"acquire:{db_path}")
        return lock_path

    def release(*, db_path: str, lock_path: str | None = None) -> None:
        events.append(f"release:{db_path}:{lock_path}")

    async def validate() -> dict:
        events.append("validate")
        return {"errors": [], "warnings": []}

    @asynccontextmanager
    async def inner(_app):
        events.append("inner-enter")
        yield
        events.append("inner-exit")

    monkeypatch.setattr(startup, "acquire_execution_process_lock", acquire)
    monkeypatch.setattr(startup, "release_execution_process_lock", release)
    monkeypatch.setattr(startup, "validate_startup", validate)
    monkeypatch.setattr(main, "_application_lifespan", inner)

    async with main.lifespan(main.app):
        events.append("serving")

    assert events == [
        f"acquire:{db_path}",
        "validate",
        "inner-enter",
        "serving",
        "inner-exit",
        f"release:{db_path}:{lock_path}",
    ]


@pytest.mark.anyio
async def test_lifespan_releases_execution_lock_when_validation_aborts(
    monkeypatch,
    tmp_path,
    anyio_backend,
):
    db_path = str(tmp_path / "invalid.db")
    lock_path = str(tmp_path / "invalid.lock")
    release = Mock()
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(
        startup,
        "acquire_execution_process_lock",
        lambda *, db_path: lock_path,
    )
    monkeypatch.setattr(startup, "release_execution_process_lock", release)
    monkeypatch.setattr(
        startup,
        "validate_startup",
        AsyncMock(side_effect=SystemExit(1)),
    )

    with pytest.raises(SystemExit):
        async with main.lifespan(main.app):
            pass

    release.assert_called_once_with(db_path=db_path, lock_path=lock_path)


@pytest.mark.anyio
async def test_sim_mode_never_starts_ibkr_runtime(
    monkeypatch,
    anyio_backend,
):
    connect = AsyncMock()
    reconnect = AsyncMock()
    monkeypatch.setattr(config.cfg, "SIM_MODE", True)
    monkeypatch.setattr(main.ibkr, "connect", connect)
    monkeypatch.setattr(main.ibkr, "start_reconnect_loop", reconnect)

    await main._start_broker_runtime()

    connect.assert_not_awaited()
    reconnect.assert_not_awaited()


@pytest.mark.anyio
async def test_known_live_port_is_blocked_before_ibkr_connect(
    monkeypatch,
    anyio_backend,
):
    connect = AsyncMock()
    monkeypatch.setattr(config.cfg, "SIM_MODE", False)
    monkeypatch.setattr(config.cfg, "IS_PAPER", True)
    monkeypatch.setattr(config.cfg, "IBKR_PORT", 7496)
    monkeypatch.setattr(main.ibkr, "connect", connect)

    with pytest.raises(RuntimeError, match="release fence"):
        await main._start_broker_runtime()

    connect.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sim_mode", "is_paper", "ibkr_port", "message"),
    [
        (True, True, 7497, "SIM_MODE"),
        (False, False, 7497, "release fence"),
        (False, True, 7496, "release fence"),
    ],
)
async def test_ibkr_client_itself_rejects_fenced_config_before_creating_ib_object(
    monkeypatch,
    anyio_backend,
    sim_mode,
    is_paper,
    ibkr_port,
    message,
):
    from ibkr_client import IBKRClient

    client = IBKRClient()
    create_ib = Mock()
    monkeypatch.setattr(config.cfg, "SIM_MODE", sim_mode)
    monkeypatch.setattr(config.cfg, "IS_PAPER", is_paper)
    monkeypatch.setattr(config.cfg, "IBKR_PORT", ibkr_port)
    monkeypatch.setattr(client, "_get_or_create_ib", create_ib)

    with pytest.raises(RuntimeError, match=message):
        await client.connect()

    create_ib.assert_not_called()


@pytest.mark.anyio
async def test_sim_mode_never_stops_ibkr_runtime(
    monkeypatch,
    anyio_backend,
):
    disconnect = AsyncMock()
    monkeypatch.setattr(config.cfg, "SIM_MODE", True)
    monkeypatch.setattr(main.ibkr, "disconnect", disconnect)

    await main._stop_broker_runtime()

    disconnect.assert_not_awaited()


@pytest.mark.anyio
async def test_manual_ibkr_connect_is_blocked_in_sim_mode(
    monkeypatch,
    anyio_backend,
):
    connect = AsyncMock()
    monkeypatch.setattr(config.cfg, "SIM_MODE", True)
    monkeypatch.setattr(status.ibkr, "connect", connect)

    with pytest.raises(HTTPException) as exc_info:
        await status.connect_ibkr(_user=object())

    assert exc_info.value.status_code == 409
    connect.assert_not_awaited()


@pytest.mark.anyio
async def test_manual_ibkr_connect_is_blocked_for_real_money_configuration(
    monkeypatch,
    anyio_backend,
):
    connect = AsyncMock()
    monkeypatch.setattr(config.cfg, "SIM_MODE", False)
    monkeypatch.setattr(config.cfg, "IS_PAPER", True)
    monkeypatch.setattr(config.cfg, "IBKR_PORT", 7496)
    monkeypatch.setattr(status.ibkr, "connect", connect)

    with pytest.raises(HTTPException) as exc_info:
        await status.connect_ibkr(_user=object())

    assert exc_info.value.status_code == 409
    assert "release fence" in str(exc_info.value.detail)
    connect.assert_not_awaited()

