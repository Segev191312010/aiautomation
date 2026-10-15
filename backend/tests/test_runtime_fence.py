"""The fence store: a stop-only global row and stop/resume identity rows, no migration."""
from __future__ import annotations

import sqlite3

import pytest

from trading.storage.runtime_fence import (
    GLOBAL_FENCE,
    IDENTITY_FENCE_PREFIX,
    SQLitePaperKillStore,
    fence_name,
)


@pytest.fixture
async def ledger(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    path = tmp_path / "fence.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


def test_fence_names_are_the_bridges_predicate_values():
    assert fence_name() == GLOBAL_FENCE == "paper"
    assert fence_name("ORB_V2") == f"{IDENTITY_FENCE_PREFIX}ORB_V2" == "paper:ORB_V2"
    assert fence_name(" ORB_V2 ") == "paper:ORB_V2"
    for bad in ("", "  ", "ORB V2"):
        with pytest.raises(ValueError):
            fence_name(bad)


async def test_identity_rows_stop_and_resume_without_touching_the_global_row(ledger):
    store = SQLitePaperKillStore()
    assert not await store.is_stopped()
    assert not await store.is_stopped(identity="ORB_V2")

    await store.stop(reason="misbehaving", identity="ORB_V2")
    assert await store.is_stopped(identity="ORB_V2")
    assert not await store.is_stopped()  # the global switch is untouched
    assert not await store.is_stopped(identity="VWAP_PB_V2")
    rows = await store.identity_rows()
    assert [(name, stopped, reason) for name, stopped, reason, _at in rows] == [("ORB_V2", True, "misbehaving")]

    await store.resume(reason="fixed", identity="ORB_V2")
    assert not await store.is_stopped(identity="ORB_V2")
    rows = await store.identity_rows()
    assert [(name, stopped, reason) for name, stopped, reason, _at in rows] == [("ORB_V2", False, "fixed")]

    # The bridge reads the very same rows with name=?.
    with sqlite3.connect(ledger) as database:
        assert database.execute(
            "SELECT stopped FROM autotrader_execution_fences WHERE name=?", ("paper:ORB_V2",)
        ).fetchone() == (0,)
        assert database.execute(
            "SELECT stopped FROM autotrader_execution_fences WHERE name='paper'"
        ).fetchone() is None


async def test_the_global_row_is_stop_only_and_reasons_are_required(ledger):
    store = SQLitePaperKillStore()
    await store.stop(reason="owner stop")
    assert await store.is_stopped()
    with pytest.raises(ValueError):
        await store.resume(reason="oops", identity=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await store.resume(reason="oops", identity="  ")
    with pytest.raises(ValueError):
        await store.stop(reason="   ", identity="ORB_V2")
    with pytest.raises(ValueError):
        await store.resume(reason="", identity="ORB_V2")
    assert await store.is_stopped()  # still stopped
    assert await store.identity_rows() == []
