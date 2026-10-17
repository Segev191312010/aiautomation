"""The fence store: a stop-only global row and stop/resume identity rows, no migration."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from pathlib import Path
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


# N15c LOW-2: how a real fence table can fail. ``missing``: renamed away;
# ``unreadable``: its name is a view over nothing, so every read and write fails.
FENCE_DAMAGE = ("missing", "unreadable")


def break_fence_table(database: Path, damage: str) -> Callable[[], None]:
    """Damage the real fence table of ``database``; returns the repair."""
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("ALTER TABLE autotrader_execution_fences RENAME TO fences_set_aside")
        if damage == "unreadable":
            connection.execute("CREATE VIEW autotrader_execution_fences AS SELECT * FROM fences_gone")
        connection.commit()

    def repair() -> None:
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("DROP VIEW IF EXISTS autotrader_execution_fences")
            connection.execute("ALTER TABLE fences_set_aside RENAME TO autotrader_execution_fences")
            connection.commit()

    return repair


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


@pytest.mark.parametrize("damage", FENCE_DAMAGE)
async def test_an_unreadable_fence_table_fails_closed_on_a_real_database(ledger, damage):
    """N15c LOW-2: on a real database whose fence table is missing or unreadable,
    nothing reports success. The auto-fence's one write (``stop_identity``) raises:
    never a kept "already fenced" row, never a write. Every other writer raises
    too, and so does ``is_stopped`` (never "not stopped"), which the manual desk
    reads as "on": buys stay blocked. Nothing is written meanwhile."""
    from trading.operations import telegram_bot

    store = SQLitePaperKillStore()
    await store.stop(reason="telegram /halt by owner", identity="GAP_GO_V2")
    repair = break_fence_table(ledger, damage)

    with pytest.raises(sqlite3.OperationalError):
        await store.stop_identity(reason="sentinel: daily loss 2.00 R > 1.25 R", identity="ORB_V2")
    with pytest.raises(sqlite3.OperationalError):
        await store.stop_identity(reason="sentinel: x", identity="GAP_GO_V2", unless=lambda _row: True)
    for identity in (None, "ORB_V2", "GAP_GO_V2"):
        with pytest.raises(sqlite3.OperationalError):
            await store.is_stopped(identity=identity)
    with pytest.raises(sqlite3.OperationalError):
        await store.identity_rows()
    with pytest.raises(sqlite3.OperationalError):
        await store.stop(reason="telegram /halt by owner", identity="ORB_V2")
    with pytest.raises(sqlite3.OperationalError):
        await store.resume(reason="owner: checked", identity="GAP_GO_V2")
    with pytest.raises(sqlite3.OperationalError):
        await store.resume(reason="owner: checked", identity="GAP_GO_V2", annotate=lambda _lifted: "owner: checked")
    desk = telegram_bot.ManualOrdersDesk(tools=None, kill_switch=store)  # type: ignore[arg-type]
    assert await desk._kill_switch_on() is True

    repair()
    rows = await store.identity_rows()
    assert [(name, stopped, reason) for name, stopped, reason, _at in rows] == [
        ("GAP_GO_V2", True, "telegram /halt by owner")
    ]
    assert not await store.is_stopped() and await store.is_stopped(identity="GAP_GO_V2")
    assert await desk._kill_switch_on() is False
