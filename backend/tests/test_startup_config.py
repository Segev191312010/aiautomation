"""Startup and config validation regressions."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from config import cfg, _validate_config
from startup import (
    DEFAULT_DEV_JWT_SECRET,
    acquire_execution_process_lock,
    release_execution_process_lock,
    validate_autopilot_matrix,
    validate_execution_topology,
    validate_startup,
)


@pytest.fixture
def restore_cfg(monkeypatch):
    # Startup tests must not inherit topology/release context from the shell
    # that happens to launch pytest.
    monkeypatch.setenv("WORKERS", "1")
    monkeypatch.setenv("ENV", "dev")
    previous = {
        "DB_PATH": cfg.DB_PATH,
        "JWT_SECRET": cfg.JWT_SECRET,
        "STRICT_CONFIG": cfg.STRICT_CONFIG,
        "AUTOPILOT_MODE": cfg.AUTOPILOT_MODE,
        "IS_PAPER": cfg.IS_PAPER,
        "IBKR_PORT": cfg.IBKR_PORT,
        "SIM_MODE": cfg.SIM_MODE,
        "IBKR_ACCOUNT_OWNER_USER_ID": cfg.IBKR_ACCOUNT_OWNER_USER_ID,
        "IBKR_PRIVATE_ACCOUNT_STREAMING_ENABLED": cfg.IBKR_PRIVATE_ACCOUNT_STREAMING_ENABLED,
    }
    try:
        yield
    finally:
        for key, value in previous.items():
            setattr(cfg, key, value)


def test_validate_config_rejects_unknown_autopilot_mode(restore_cfg):
    cfg.AUTOPILOT_MODE = "PAPRE"

    with pytest.raises(ValueError, match="AUTOPILOT_MODE='PAPRE' is invalid"):
        _validate_config(cfg)


@pytest.mark.anyio
async def test_validate_startup_warns_on_default_jwt_secret_off_mode(
    restore_cfg,
    anyio_backend,
    tmp_path,
):
    """With AUTOPILOT_MODE=OFF, default JWT_SECRET is a warning (not error)."""
    cfg.DB_PATH = str(tmp_path / "startup-warning.db")
    cfg.JWT_SECRET = DEFAULT_DEV_JWT_SECRET
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "OFF"
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = False

    result = await validate_startup()

    assert any("containment floor" in w for w in result["warnings"])
    assert not result["errors"]


@pytest.mark.anyio
async def test_validate_startup_fatally_rejects_weak_secret_in_paper_mode(
    restore_cfg,
    anyio_backend,
    tmp_path,
):
    """Safety errors cannot be bypassed with STRICT_CONFIG=false."""
    cfg.DB_PATH = str(tmp_path / "startup-fatal.db")
    cfg.JWT_SECRET = DEFAULT_DEV_JWT_SECRET
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "PAPER"
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = False

    with pytest.raises(SystemExit) as exc_info:
        await validate_startup()
    assert exc_info.value.code == 1


@pytest.mark.anyio
async def test_private_ibkr_streaming_requires_explicit_operator_owner(
    restore_cfg,
    tmp_path,
    anyio_backend,
):
    cfg.DB_PATH = str(tmp_path / "private-streaming.db")
    cfg.JWT_SECRET = "strong-random-secret"
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "OFF"
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = False
    cfg.IBKR_PRIVATE_ACCOUNT_STREAMING_ENABLED = True
    cfg.IBKR_ACCOUNT_OWNER_USER_ID = ""

    with pytest.raises(SystemExit) as exc_info:
        await validate_startup()
    assert exc_info.value.code == 1

    cfg.IBKR_ACCOUNT_OWNER_USER_ID = "operator"
    result = await validate_startup()
    assert not any("IBKR_ACCOUNT_OWNER_USER_ID" in error for error in result["errors"])


# ── Autopilot matrix validator ───────────────────────────────────────────────


def _matrix(**overrides) -> list[str]:
    kwargs: dict = dict(
        mode="OFF",
        is_paper=True,
        sim_mode=False,
        jwt_secret="test-only-random-secret-at-least-32-bytes",
        jwt_bootstrap_secret=None,
        ibkr_port=7497,
    )
    kwargs.update(overrides)
    return validate_autopilot_matrix(**kwargs)


def test_matrix_off_mode_always_safe():
    assert _matrix(mode="OFF", jwt_secret=DEFAULT_DEV_JWT_SECRET) == []


def test_matrix_off_live_broker_requires_strong_auth():
    errors = _matrix(
        mode="OFF",
        is_paper=False,
        sim_mode=False,
        jwt_secret=DEFAULT_DEV_JWT_SECRET,
    )
    assert any("at least 32 bytes" in error for error in errors)
    assert any("Real-money broker connectivity" in error for error in errors)


def test_matrix_off_live_broker_rejects_bootstrap_auth():
    errors = _matrix(
        mode="OFF",
        is_paper=False,
        sim_mode=False,
        jwt_bootstrap_secret="dev-bootstrap",
    )
    assert any("JWT_BOOTSTRAP_SECRET" in error for error in errors)


def test_matrix_paper_rejects_real_money_broker():
    errors = _matrix(mode="PAPER", is_paper=False, sim_mode=False)
    assert any("AUTOPILOT_MODE=PAPER" in error for error in errors)


def test_matrix_rejects_paper_flag_with_known_live_port():
    errors = _matrix(
        mode="OFF",
        is_paper=True,
        sim_mode=False,
        ibkr_port=7496,
    )
    assert any("Real-money broker connectivity" in error for error in errors)
    assert any("flag/port mismatch" in error for error in errors)


def test_matrix_unknown_mode_rejected():
    errors = _matrix(mode="WILD")
    assert any("invalid" in e.lower() for e in errors)


def test_matrix_paper_requires_strong_jwt():
    errors = _matrix(mode="PAPER", jwt_secret=DEFAULT_DEV_JWT_SECRET)
    assert any("JWT_SECRET" in e for e in errors)


@pytest.mark.parametrize("weak_secret", ["", "x", "short-secret", "a" * 31])
def test_matrix_rejects_empty_or_short_jwt_secrets(weak_secret):
    errors = _matrix(mode="LIVE", is_paper=False, jwt_secret=weak_secret)
    assert any("at least 32 bytes" in error for error in errors)


def test_matrix_live_rejects_is_paper_broker():
    errors = _matrix(mode="LIVE", is_paper=True, sim_mode=False)
    assert any("IS_PAPER=true" in e for e in errors)


def test_matrix_live_rejects_sim_mode():
    errors = _matrix(mode="LIVE", is_paper=False, sim_mode=True)
    assert any("SIM_MODE=true" in e for e in errors)


def test_matrix_live_rejects_bootstrap_secret_present():
    errors = _matrix(
        mode="LIVE",
        is_paper=False,
        sim_mode=False,
        jwt_bootstrap_secret="anything-set",
    )
    assert any("JWT_BOOTSTRAP_SECRET" in e for e in errors)


def test_matrix_live_is_blocked_by_stage_9a_release_fence():
    errors = _matrix(
        mode="LIVE",
        is_paper=False,
        sim_mode=False,
        jwt_secret="test-only-random-secret-at-least-32-bytes",
        jwt_bootstrap_secret=None,
    )
    assert any("Stage 9A release fence" in error for error in errors)


# ── Execution topology ───────────────────────────────────────────────────────


def test_single_worker_topology_is_supported():
    assert validate_execution_topology(workers=1) == []


@pytest.mark.parametrize("workers", [0, 2, 4])
def test_non_single_worker_topology_is_rejected(workers):
    errors = validate_execution_topology(workers=workers)
    assert len(errors) == 1
    assert "exactly one Uvicorn worker" in errors[0]


@pytest.mark.anyio
async def test_topology_violation_is_fatal_even_when_config_not_strict(
    restore_cfg,
    anyio_backend,
    monkeypatch,
    tmp_path,
):
    cfg.DB_PATH = str(tmp_path / "topology.db")
    cfg.JWT_SECRET = DEFAULT_DEV_JWT_SECRET
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "OFF"
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = True
    monkeypatch.setenv("WORKERS", "2")

    with pytest.raises(SystemExit) as exc_info:
        await validate_startup()

    assert exc_info.value.code == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "db_path",
    [
        "",
        "   ",
        ":memory:",
        "file::memory:?cache=shared",
        "file:rate-cap?mode=memory&cache=shared",
    ],
)
async def test_ephemeral_database_is_fatally_rejected(
    restore_cfg,
    anyio_backend,
    monkeypatch,
    db_path,
):
    cfg.DB_PATH = db_path
    cfg.JWT_SECRET = "test-only-random-secret-at-least-32-bytes"
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "OFF"
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = True
    monkeypatch.setenv("WORKERS", "1")

    with pytest.raises(SystemExit) as exc_info:
        await validate_startup()

    assert exc_info.value.code == 1


def test_execution_process_lock_blocks_undeclared_second_worker(tmp_path):
    """The runtime lock catches `uvicorn --workers 2` even if WORKERS is unset."""
    db_path = str(tmp_path / "owner.db")
    lock_path = str(tmp_path / "owner.lock")
    acquire_execution_process_lock(db_path=db_path, lock_path=lock_path)
    script = (
        "import sys\n"
        "from startup import acquire_execution_process_lock\n"
        "try:\n"
        "    acquire_execution_process_lock(db_path=sys.argv[1], lock_path=sys.argv[2])\n"
        "except RuntimeError:\n"
        "    raise SystemExit(23)\n"
    )
    try:
        blocked = subprocess.run(
            [sys.executable, "-c", script, db_path, lock_path],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )
        assert blocked.returncode == 23
    finally:
        release_execution_process_lock(db_path=db_path, lock_path=lock_path)

    succeeds_after_release = subprocess.run(
        [sys.executable, "-c", script, db_path, lock_path],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert succeeds_after_release.returncode == 0


def test_execution_process_lock_rejects_second_lifespan_in_same_process(tmp_path):
    db_path = str(tmp_path / "same-process.db")
    lock_path = str(tmp_path / "same-process.lock")
    acquire_execution_process_lock(db_path=db_path, lock_path=lock_path)
    try:
        with pytest.raises(RuntimeError, match="already owns"):
            acquire_execution_process_lock(db_path=db_path, lock_path=lock_path)
    finally:
        release_execution_process_lock(db_path=db_path, lock_path=lock_path)
