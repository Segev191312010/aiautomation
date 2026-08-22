"""Database path authority and simulation lifecycle regressions."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import aiosqlite
import pytest

from config import cfg
from simulation import SimEngine, sim_engine


REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH_GUARD = REPO_ROOT / "scripts" / "check_db_path.sh"


def _run_db_path_guard(
    mode: str | None,
    *,
    db_path: str | None = None,
    cwd: Path | None = None,
    guard: Path = DB_PATH_GUARD,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if db_path is None:
        env.pop("DB_PATH", None)
    else:
        env["DB_PATH"] = db_path
    env["PYTHON_BIN"] = sys.executable
    env["PYTHON_DOTENV_DISABLED"] = "1"
    if env_overrides:
        env.update(env_overrides)
    command = [str(guard)]
    if mode is not None:
        command.append(mode)
    return subprocess.run(
        command,
        cwd=cwd or REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_docker_path_is_data_volume() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'DB_PATH: "/data/trading_bot.db"' in compose
    assert "trading-data:/data" in compose
    for dockerfile in (REPO_ROOT / "backend" / "Dockerfile", REPO_ROOT / "Dockerfile"):
        contents = dockerfile.read_text(encoding="utf-8")
        assert "ENV DB_PATH=/data/trading_bot.db" in contents
        assert "check_db_path.sh --docker" in contents

    result = _run_db_path_guard("--docker", db_path="/data/trading_bot.db")

    assert result.returncode == 0, result.stderr
    assert "mode=docker" in result.stdout
    assert "canonical=/data/trading_bot.db" in result.stdout


def test_db_guard_host_mode_uses_backend_default() -> None:
    result = _run_db_path_guard("--host")

    assert result.returncode == 0, result.stderr
    assert "mode=host" in result.stdout
    assert f"canonical={REPO_ROOT / 'backend' / 'trading_bot.db'}" in result.stdout


def test_db_guard_rejects_mismatched_docker_path() -> None:
    configured_path = "/tmp/not-the-volume.db"
    result = _run_db_path_guard("--docker", db_path=configured_path)

    assert result.returncode == 1
    assert "DB_PATH" in result.stderr
    assert configured_path not in result.stderr


def test_db_guard_rejects_missing_mode() -> None:
    result = _run_db_path_guard(None)

    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_db_guard_rejects_unknown_mode() -> None:
    result = _run_db_path_guard("--unknown")

    assert result.returncode == 2
    assert "Usage:" in result.stderr


@pytest.mark.parametrize("mode", ["host", "docker"])
def test_db_guard_rejects_bare_modes(mode: str) -> None:
    result = _run_db_path_guard(mode)

    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_unknown_mode_precedes_legacy_path_check(tmp_path: Path) -> None:
    isolated_repo = tmp_path / "repo"
    scripts_dir = isolated_repo / "scripts"
    scripts_dir.mkdir(parents=True)
    isolated_guard = scripts_dir / "check_db_path.sh"
    shutil.copy2(DB_PATH_GUARD, isolated_guard)
    (isolated_repo / "trading_bot.db").touch()

    result = _run_db_path_guard(
        "--unknown",
        cwd=isolated_repo,
        guard=isolated_guard,
    )

    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "legacy database path" not in result.stderr


@pytest.mark.parametrize(
    "configured_path",
    [":memory:", "file::memory:?cache=shared", "file:/tmp/trading.db"],
)
def test_host_guard_rejects_sqlite_uri_paths(configured_path: str) -> None:
    result = _run_db_path_guard("--host", db_path=configured_path)

    assert result.returncode == 1
    assert result.stderr == "ERROR: DB_PATH must be a plain filesystem path\n"
    assert configured_path not in result.stdout
    assert configured_path not in result.stderr


@pytest.mark.parametrize(
    "configured_path",
    ["safe.db\n::error::injected", "safe.db\rrewritten", "safe.db\x1b[31m"],
)
def test_host_guard_rejects_control_characters_without_echoing_them(
    configured_path: str,
) -> None:
    result = _run_db_path_guard("--host", db_path=configured_path)

    assert result.returncode == 1
    assert result.stderr == "ERROR: DB_PATH must be a plain filesystem path\n"
    assert configured_path not in result.stdout
    assert configured_path not in result.stderr
    assert "::error::" not in result.stdout + result.stderr


def test_host_guard_suppresses_config_tracebacks() -> None:
    injected_value = "not-a-port\n::error::injected"

    result = _run_db_path_guard(
        "--host",
        db_path="safe.db",
        env_overrides={"IBKR_PORT": injected_value},
    )

    assert result.returncode == 1
    assert result.stderr == (
        "ERROR: unable to resolve DB_PATH through backend/config.py\n"
    )
    assert injected_value not in result.stdout
    assert injected_value not in result.stderr
    assert "Traceback" not in result.stderr


def test_host_guard_normalizes_relative_python_bin() -> None:
    relative_python = os.path.relpath(sys.executable, REPO_ROOT)

    result = _run_db_path_guard(
        "--host",
        env_overrides={"PYTHON_BIN": relative_python},
    )

    assert result.returncode == 0, result.stderr
    assert f"canonical={REPO_ROOT / 'backend' / 'trading_bot.db'}" in result.stdout


@pytest.mark.parametrize(
    "configured_path",
    [None, "  ", "relative.db", "../outside.db", "~/outside.db"],
)
def test_host_guard_matches_runtime_path_resolution(
    configured_path: str | None,
) -> None:
    result = _run_db_path_guard("--host", db_path=configured_path)
    assert result.returncode == 0, result.stderr

    env = os.environ.copy()
    if configured_path is None:
        env.pop("DB_PATH", None)
    else:
        env["DB_PATH"] = configured_path
    env["PYTHONPATH"] = str(REPO_ROOT / "backend")
    env["PYTHON_DOTENV_DISABLED"] = "1"
    expected = subprocess.check_output(
        [sys.executable, "-c", "from config import cfg; print(cfg.DB_PATH)"],
        cwd=REPO_ROOT / "backend",
        env=env,
        text=True,
    ).strip()

    assert f"canonical={expected}" in result.stdout


def test_legacy_root_db_rejected(tmp_path: Path) -> None:
    isolated_repo = tmp_path / "repo"
    scripts_dir = isolated_repo / "scripts"
    backend_dir = isolated_repo / "backend"
    scripts_dir.mkdir(parents=True)
    backend_dir.mkdir()
    isolated_guard = scripts_dir / "check_db_path.sh"
    shutil.copy2(DB_PATH_GUARD, isolated_guard)
    (isolated_repo / "trading_bot.db").touch()
    (backend_dir / "trading_bot.db").touch()

    result = _run_db_path_guard(
        "--host",
        cwd=isolated_repo,
        guard=isolated_guard,
    )

    assert result.returncode == 1
    assert "legacy database path exists" in result.stderr


def test_dangling_legacy_root_db_symlink_rejected(tmp_path: Path) -> None:
    isolated_repo = tmp_path / "repo"
    scripts_dir = isolated_repo / "scripts"
    scripts_dir.mkdir(parents=True)
    isolated_guard = scripts_dir / "check_db_path.sh"
    shutil.copy2(DB_PATH_GUARD, isolated_guard)
    (isolated_repo / "trading_bot.db").symlink_to("missing.db")

    result = _run_db_path_guard(
        "--host",
        cwd=isolated_repo,
        guard=isolated_guard,
    )

    assert result.returncode == 1
    assert "legacy database path exists" in result.stderr


@pytest.mark.parametrize(
    "configured_path",
    [
        "../trading_bot.db",
        "../TRADING_BOT.DB",
        str(REPO_ROOT / "trading_bot.db"),
        str(REPO_ROOT / "TRADING_BOT.DB"),
        str(Path(str(REPO_ROOT).swapcase()) / "TRADING_BOT.DB"),
    ],
)
def test_legacy_root_db_configuration_rejected(configured_path: str) -> None:
    assert not (REPO_ROOT / "trading_bot.db").exists()

    result = _run_db_path_guard("--host", db_path=configured_path)

    assert result.returncode != 0
    assert "DB_PATH resolves to the forbidden legacy database path" in result.stderr


def test_no_module_hardcodes_db_filename() -> None:
    offenders = []
    backend_dir = REPO_ROOT / "backend"
    tracked_backend_files = subprocess.check_output(
        ["git", "ls-files", "backend"],
        cwd=REPO_ROOT,
        text=True,
    ).splitlines()
    for relative_path in tracked_backend_files:
        path = REPO_ROOT / relative_path
        if path.suffix != ".py":
            continue
        if path == backend_dir / "config.py" or "tests" in path.parts:
            continue
        if "trading_bot.db" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_default_database_path_is_absolute_and_cwd_independent() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    repo_root = backend_dir.parent
    env = os.environ.copy()
    env.pop("DB_PATH", None)
    env["PYTHONPATH"] = str(backend_dir)
    env["PYTHON_DOTENV_DISABLED"] = "1"
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
    env["PYTHON_DOTENV_DISABLED"] = "1"

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
    first_result = await engine.execute_order(
        "AAPL", "BUY", 1, 10, user_id="database-path-test"
    )
    assert first_result[0] is True

    monkeypatch.setattr(cfg, "DB_PATH", second_path)
    second_result = await engine.execute_order(
        "MSFT", "BUY", 1, 20, user_id="database-path-test"
    )
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
    result = await engine.execute_order(
        "NVDA", "BUY", 1, 30, user_id="database-path-test"
    )

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
