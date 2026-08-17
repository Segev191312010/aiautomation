"""Database path authority and simulation lifecycle regressions."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import aiosqlite
import pytest

from config import cfg
from simulation import SimEngine, sim_engine


def test_default_database_path_is_absolute_and_cwd_independent() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    repo_root = backend_dir.parent
    env = os.environ.copy()
    env.pop("DB_PATH", None)
    env["PYTHONPATH"] = str(backend_dir)
    command = [sys.executable, "-c", "from config import cfg; print(cfg.DB_PATH)"]

    from_backend = subprocess.check_output(
        command, cwd=backend_dir, env=env, text=True
    ).strip()
    from_repo_root = subprocess.check_output(
        command, cwd=repo_root, env=env, text=True
    ).strip()

    assert from_backend == from_repo_root
    assert Path(from_backend) == backend_dir / "trading_bot.db"
    assert Path(from_backend).is_absolute()


def test_database_facade_does_not_export_a_stale_path_snapshot() -> None:
    import database

    assert not hasattr(database, "DB_PATH")


def test_ephemeral_database_configuration_remains_visible_to_startup_guard() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["DB_PATH"] = ":memory:"
    env["PYTHONPATH"] = str(backend_dir)

    configured = subprocess.check_output(
        [sys.executable, "-c", "from config import cfg; print(cfg.DB_PATH)"],
        cwd=backend_dir,
        env=env,
        text=True,
    ).strip()

    assert configured == ":memory:"


@pytest.mark.anyio
async def test_sim_engine_follows_runtime_config_path_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anyio_backend: str,
) -> None:
    first_path = str(tmp_path / "first.db")
    second_path = str(tmp_path / "second.db")
    engine = SimEngine()

    monkeypatch.setattr(cfg, "DB_PATH", first_path)
    await engine.initialize()
    first_result = await engine.execute_order("AAPL", "BUY", 1, 10)
    assert first_result[0] is True

    monkeypatch.setattr(cfg, "DB_PATH", second_path)
    second_result = await engine.execute_order("MSFT", "BUY", 1, 20)
    assert second_result[0] is True

    async with aiosqlite.connect(first_path) as db:
        first_symbols = await (await db.execute(
            "SELECT symbol FROM sim_orders ORDER BY symbol"
        )).fetchall()
    async with aiosqlite.connect(second_path) as db:
        second_symbols = await (await db.execute(
            "SELECT symbol FROM sim_orders ORDER BY symbol"
        )).fetchall()

    assert first_symbols == [("AAPL",)]
    assert second_symbols == [("MSFT",)]


@pytest.mark.anyio
async def test_explicit_sim_engine_path_remains_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anyio_backend: str,
) -> None:
    explicit_path = str(tmp_path / "explicit.db")
    unrelated_path = str(tmp_path / "unrelated.db")
    engine = SimEngine(db_path=explicit_path)

    monkeypatch.setattr(cfg, "DB_PATH", unrelated_path)
    await engine.initialize()
    result = await engine.execute_order("NVDA", "BUY", 1, 30)

    assert result[0] is True
    assert Path(explicit_path).exists()
    assert not Path(unrelated_path).exists()


@pytest.mark.anyio
async def test_global_sim_engine_never_reuses_a_deleted_fixture_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anyio_backend: str,
) -> None:
    transient_path = tmp_path / "transient.db"
    current_path = tmp_path / "current.db"

    monkeypatch.setattr(cfg, "DB_PATH", str(transient_path))
    await sim_engine.initialize()
    transient_path.unlink()

    monkeypatch.setattr(cfg, "DB_PATH", str(current_path))
    await sim_engine.initialize()
    account = await sim_engine.get_account()

    assert account.initial_cash == cfg.SIM_INITIAL_CASH
    assert current_path.exists()
    assert not transient_path.exists()
