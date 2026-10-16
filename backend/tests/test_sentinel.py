"""The Sentinel (T-9): a deterministic, stop-only auto-fence from structured bridge state.

Every state file here is written by the pinned bridge's own StateStore and
Bracket (tests.test_paper_bridge loads scripts_local/paper_bridge.py), so the
Sentinel reads exactly what the bridge writes. Fences go to a scratch database
(conftest isolates HOME and DB_PATH); nothing touches the live state.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import inspect
import os
from pathlib import Path
import sqlite3

import pytest

from tests.test_paper_bridge import pb as bridge
from trading.operations import sentinel, supervisor
from trading.operations.sentinel import (
    BLIND_AFTER,
    BLIND_REPEAT,
    CAPPED_LOSS_FACTOR,
    LOSING_STREAK,
    MAX_ENTRIES_PER_DAY,
    UNCAPPED_LOSS_R,
    Sentinel,
    blind_text,
    bracket_r,
    daily_loss,
    daily_loss_trigger,
    fence_identity,
    fence_text,
    loss_limit,
    owner_resume_reason,
    owner_resumed_on,
    read_bridge_state,
    streak_triggers,
)
from trading.research.live_ledger import read_live_ledger
from trading.scheduler.sessions import MARKET_TIMEZONE
from trading.sendable_identities import SENDABLE_IDENTITIES
from trading.storage.runtime_fence import FenceRow, SQLitePaperKillStore

FRIDAY = date(2026, 10, 9)
MONDAY = date(2026, 10, 12)
TUESDAY = date(2026, 10, 13)
TODAY = date(2026, 10, 14)  # a Wednesday session


def _et(hour: int, minute: int, second: int = 0, *, day: date = TODAY) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=MARKET_TIMEZONE).astimezone(timezone.utc)


def _bracket(decision_id: str, identity: str = "ORB_V2", **values) -> object:
    """1 R = 100 sh x (50.00 - 49.00) = $100 unless overridden."""
    base = bridge.Bracket(
        decision_id=decision_id,
        symbol="ABC",
        con_id=4931,
        primary_exchange="NYSE",
        strategy="orb",
        qty=100,
        entry="50.00",
        stop="49.00",
        target="52.00",
        parent=101,
        target_id=102,
        stop_id=103,
        status="sent",
        strategy_name=identity,
        sent_at=_et(10, 0).isoformat(),
    )
    return replace(base, **values)


def _closed(decision_id: str, identity: str, pnl: str, *, day: date, at: time = time(10, 0)) -> object:
    """A closed 100-share bracket that made ``pnl`` (avg exit = 50 + pnl / 100)."""
    exit_price = Decimal("50.00") + Decimal(pnl) / 100
    return _bracket(
        decision_id, identity, status="closed", filled=100, exit_filled=100,
        avg_entry="50.0000", avg_exit=str(exit_price), realized_pnl=pnl,
        exit_reason="stop" if Decimal(pnl) < 0 else "target",
        sent_at=datetime.combine(day, at, tzinfo=MARKET_TIMEZONE).isoformat(),
    )


def _save(directory: Path, day: date, brackets: list, *, written_at: datetime | None = None) -> Path:
    store = bridge.StateStore(state_dir=directory, legacy_path=None, session_date=day)
    store.save({item.decision_id: item for item in brackets})
    if written_at is not None:
        stamp = written_at.timestamp()
        os.utime(store.path, (stamp, stamp))
    return store.path


def _submitted(brackets: list) -> dict:
    from dataclasses import asdict

    return {item.decision_id: asdict(item) for item in brackets}


@pytest.fixture
async def fences(tmp_path, monkeypatch) -> Path:
    from config import cfg
    from db.core import init_db

    path = tmp_path / "sentinel.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


def _rows(database: Path) -> dict[str, tuple[int, str, str]]:
    with sqlite3.connect(database) as connection:
        return {
            name: (stopped, reason, updated)
            for name, stopped, reason, updated in connection.execute(
                "SELECT name, stopped, reason, updated_at FROM autotrader_execution_fences"
            )
        }


class Heartbeat:
    def __init__(self, at: datetime | None = None) -> None:
        self.at = at
        self.raises = False

    async def __call__(self) -> datetime | None:
        if self.raises:
            raise OSError("database is locked")
        return self.at


class RecordingStore:
    """Fails the test on any resume or any global-row write; records the stops."""

    def __init__(self) -> None:
        self.stops: list[tuple[str | None, str]] = []
        self.stopped: set[str] = set()

    async def stop_identity(self, *, reason: str, identity: str, unless=None) -> FenceRow | None:
        assert identity is not None, "the Sentinel wrote the global paper row"
        if identity in self.stopped:
            return FenceRow(stopped=True, reason="kept", updated_at=_et(9, 0).isoformat())
        self.stops.append((identity, reason))
        self.stopped.add(identity)
        return None

    async def resume(self, **_kwargs) -> None:  # pragma: no cover - must never run
        raise AssertionError("the Sentinel resumed a fence")


def _sentinel(
    directory: Path,
    memory: dict,
    *,
    store=None,
    heartbeat: Heartbeat | None = None,
    day: date = TODAY,
) -> Sentinel:
    return Sentinel(
        session_date=day,
        state_directory=directory,
        memory=memory,
        store=store or SQLitePaperKillStore(),
        read_heartbeat=heartbeat or Heartbeat(),
    )


# --------------------------------------------------------------------------
# The owner's numbers
# --------------------------------------------------------------------------


def test_the_thresholds_are_the_owner_accepted_defaults_and_the_bridges_caps():
    assert CAPPED_LOSS_FACTOR == Decimal("1.25")
    assert UNCAPPED_LOSS_R == Decimal("2")
    assert LOSING_STREAK == 3
    assert (BLIND_AFTER, BLIND_REPEAT) == (timedelta(minutes=5), timedelta(minutes=30))
    # One caps row per sendable identity, equal to the pinned bridge's own table.
    assert set(MAX_ENTRIES_PER_DAY) == set(SENDABLE_IDENTITIES)
    assert dict(MAX_ENTRIES_PER_DAY) == {
        name: caps.max_entries_per_day for name, caps in bridge.IDENTITY_CAPS.items()
    }
    assert {name: loss_limit(name) for name in SENDABLE_IDENTITIES} == {
        "ORB_V2": (Decimal("1.25"), False),
        "GAP_GO_V2": (Decimal("1.25"), False),
        "VWAP_PB_V2": (Decimal("2.50"), False),
        "MOMO_HOD_V2": (Decimal("2"), True),
    }
    # A name the table lacks is held to the tightest limit, never a wider one.
    assert loss_limit("SOMEDAY_V2") == (Decimal("1.25"), False)
    # Blindness is judged only while the supervisor runs the bridge.
    assert sentinel.WATCH_FROM == supervisor.BRIDGE_START
    assert sentinel.WATCH_UNTIL_BEFORE_CLOSE == supervisor.BRIDGE_FINISH_BEFORE_CLOSE


# --------------------------------------------------------------------------
# Daily loss (pure)
# --------------------------------------------------------------------------


def test_daily_loss_is_each_brackets_pnl_over_its_own_r_against_the_identitys_limit():
    # ORB_V2: 1 entry a day, so 1.25 R; strictly more trips it.
    at_limit = _submitted([_bracket("orb", status="closed", filled=100, exit_filled=100, realized_pnl="-125.00")])
    assert daily_loss_trigger("ORB_V2", at_limit) is None
    over = _submitted([_bracket("orb", status="closed", filled=100, exit_filled=100, realized_pnl="-126.00")])
    trigger = daily_loss_trigger("ORB_V2", over)
    assert trigger is not None
    assert trigger.reason == "sentinel: daily loss 1.26 R > 1.25 R (realized -$126.00)"
    assert fence_text(trigger) == (
        "⛔ auto-fence ORB_V2: daily loss 1.26 R > 1.25 R (realized -$126.00) — new entries blocked;"
        " resume: autotraderctl paper resume --identity ORB_V2"
    )
    # An ordinary 1 R stop is the design: no fence.
    assert daily_loss_trigger("ORB_V2", _submitted([_bracket("orb", realized_pnl="-100.00")])) is None

    # VWAP_PB_V2 (2 entries: 2.5 R): two brackets of different size, each in its own R.
    # $194 is under 2.5 x the larger R ($250), but 1.5 R + 1.1 R = 2.6 R is over.
    small = dict(qty=40, entry="20.00", stop="19.00")  # 1 R = $40
    vwap = [
        _bracket("v1", "VWAP_PB_V2", status="closed", realized_pnl="-150.00"),
        _bracket("v2", "VWAP_PB_V2", status="closed", realized_pnl="-44.00", **small),
    ]
    trigger = daily_loss_trigger("VWAP_PB_V2", _submitted(vwap))
    assert trigger is not None and trigger.reason == "sentinel: daily loss 2.60 R > 2.5 R (realized -$194.00)"
    # The rule is the day's realized loss: a win offsets it.
    won = [*vwap, _bracket("v3", "VWAP_PB_V2", status="closed", realized_pnl="50.00")]
    assert daily_loss_trigger("VWAP_PB_V2", _submitted(won)) is None

    # MOMO_HOD_V2 is uncapped: 2 R or more.
    momo = _submitted([_bracket("m1", "MOMO_HOD_V2", status="closed", realized_pnl="-200.00")])
    trigger = daily_loss_trigger("MOMO_HOD_V2", momo)
    assert trigger is not None and trigger.reason == "sentinel: daily loss 2.00 R ≥ 2 R (realized -$200.00)"
    assert daily_loss_trigger(
        "MOMO_HOD_V2", _submitted([_bracket("m1", "MOMO_HOD_V2", realized_pnl="-199.99")])
    ) is None


def test_only_sendable_identities_count_and_skipped_or_shapeless_brackets_never_do():
    huge = "-10000.00"
    others = _submitted([
        _bracket("v1", "MOMO_HOD_V1", realized_pnl=huge),
        _bracket("ap", "AP_MOMO_HOD_V1", realized_pnl=huge),
        _bracket("legacy", "", realized_pnl=huge),  # before bridge v7: no identity
        _bracket("case", "orb_v2", realized_pnl=huge),  # the bridge's match is exact
        _bracket("skip", "ORB_V2", skipped=True, status="skipped", realized_pnl=huge),
    ])
    assert [daily_loss_trigger(name, others) for name in SENDABLE_IDENTITIES] == [None] * 4
    assert bracket_r({"qty": 0, "entry": "50", "stop": "49"}) is None
    assert bracket_r({"qty": 100, "entry": "49", "stop": "50"}) is None
    assert bracket_r({"qty": 100, "entry": "50.00", "stop": "49.50"}) == Decimal("50.0000")


def test_a_bracket_without_a_usable_r_is_measured_in_the_identitys_largest_r_today():
    """N15a LOW-6 (mutation M16): the lenient unit. VWAP_PB_V2 (2.5 R) with a $100 R
    and a $40 R bracket, and a $150 loss on a bracket with no entry: in the larger
    R it is 1.5 R (2.0 R in all: no fence); in the smaller it would be 3.75 R."""
    brackets = _submitted([
        _bracket("big", "VWAP_PB_V2", status="closed", realized_pnl="-50.00"),  # 1 R = $100
        _bracket("small", "VWAP_PB_V2", status="closed", realized_pnl="0", qty=40, entry="20.00", stop="19.00"),
        _bracket("shapeless", "VWAP_PB_V2", status="closed", realized_pnl="-150.00", entry=None),
    ])
    assert bracket_r(brackets["small"]) == Decimal("40.00") and bracket_r(brackets["shapeless"]) is None
    assert daily_loss("VWAP_PB_V2", brackets) == (Decimal("2.0"), Decimal("-200.00"))
    assert daily_loss_trigger("VWAP_PB_V2", brackets) is None
    # Only a shapeless bracket: nothing to measure it in, never a guess.
    alone = _submitted([_bracket("shapeless", "VWAP_PB_V2", status="closed", realized_pnl="-150.00", entry=None)])
    assert daily_loss("VWAP_PB_V2", alone) is None


# --------------------------------------------------------------------------
# Losing streak (the live ledger, across sessions)
# --------------------------------------------------------------------------


def test_three_losses_in_a_row_across_sessions_fence_on_the_day_the_third_closes(tmp_path):
    directory = tmp_path / "bridge"
    _save(directory, MONDAY, [_closed("m-loss", "ORB_V2", "-100.00", day=MONDAY)])
    _save(directory, TUESDAY, [_closed("t-loss", "ORB_V2", "-100.00", day=TUESDAY)])
    # Two so far: nothing.
    assert streak_triggers(directory, TUESDAY, read_live_ledger) == ()
    _save(directory, TODAY, [_closed("w-loss", "ORB_V2", "-101.00", day=TODAY)])

    (trigger,) = streak_triggers(directory, TODAY, read_live_ledger)
    assert trigger.identity == "ORB_V2"
    assert trigger.reason.startswith("sentinel: losing streak 3 losing trades in a row (-1.0")
    assert trigger.reason.endswith(" R net)")
    # The next day, with no new trade, the old streak does not fire again
    # (an owner's resume holds until the next loss).
    assert streak_triggers(directory, date(2026, 10, 15), read_live_ledger) == ()
    # A win in between breaks it.
    _save(directory, TUESDAY, [_closed("t-win", "ORB_V2", "120.00", day=TUESDAY)])
    assert streak_triggers(directory, TODAY, read_live_ledger) == ()


def test_trades_are_ordered_by_entry_time_not_by_decision_id(tmp_path):
    directory = tmp_path / "bridge"
    _save(directory, MONDAY, [_closed("m-win", "VWAP_PB_V2", "100.00", day=MONDAY)])
    # In entry order Tuesday is win, loss, loss; by decision id it would be loss, loss, win.
    _save(directory, TUESDAY, [
        _closed("z-win", "VWAP_PB_V2", "100.00", day=TUESDAY, at=time(10, 0)),
        _closed("a-loss", "VWAP_PB_V2", "-100.00", day=TUESDAY, at=time(11, 0)),
        _closed("b-loss", "VWAP_PB_V2", "-100.00", day=TUESDAY, at=time(12, 0)),
    ])
    _save(directory, TODAY, [_closed("c-loss", "VWAP_PB_V2", "-100.00", day=TODAY)])

    (trigger,) = streak_triggers(directory, TODAY, read_live_ledger)
    assert trigger.identity == "VWAP_PB_V2" and "3 losing trades in a row" in trigger.reason


# --------------------------------------------------------------------------
# The Sentinel: one fence per identity per day, stop-only, identity rows only
# --------------------------------------------------------------------------


async def test_a_trigger_writes_its_identity_row_once_per_day_and_an_owner_resume_holds(tmp_path, fences):
    directory = tmp_path / "bridge"
    _save(directory, TODAY, [_bracket("orb", status="closed", realized_pnl="-130.00")], written_at=_et(10, 0))
    memory: dict[str, str] = {}
    heartbeat = Heartbeat(_et(10, 0))
    watcher = _sentinel(directory, memory, heartbeat=heartbeat)

    messages = await watcher.check(_et(10, 0, 30))

    reason = "sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)"
    assert messages == [(
        "sentinel_fence",
        f"⛔ auto-fence ORB_V2: daily loss 1.30 R > 1.25 R (realized -$130.00) — new entries blocked;"
        " resume: autotraderctl paper resume --identity ORB_V2",
    )]
    rows = _rows(fences)
    assert {name: row[:2] for name, row in rows.items()} == {"paper:ORB_V2": (1, reason)}
    assert "paper" not in rows and not await SQLitePaperKillStore().is_stopped()
    # The bridge's own predicate sees the stop.
    assert bridge.identity_fence_active(fences, "ORB_V2")

    # Still over the limit a minute later: no second write, no second message.
    written = rows["paper:ORB_V2"][2]
    heartbeat.at = _et(10, 1)
    assert await watcher.check(_et(10, 1, 30)) == []
    assert _rows(fences)["paper:ORB_V2"][2] == written

    # The owner resumes: the Sentinel never re-fences that day, even after a restart.
    await SQLitePaperKillStore().resume(reason="owner: checked", identity="ORB_V2")
    assert await watcher.check(_et(10, 2)) == []
    restarted = _sentinel(directory, memory, heartbeat=Heartbeat(_et(10, 3)))
    assert await restarted.check(_et(10, 3)) == []
    assert _rows(fences)["paper:ORB_V2"][:2] == (0, "owner: checked")

    # The next day is a new day (fresh per-date memory): a new loss fences again.
    tomorrow = date(2026, 10, 15)
    _save(directory, tomorrow, [_bracket("orb2", status="closed", realized_pnl="-200.00")],
          written_at=_et(10, 0, day=tomorrow))
    next_day = _sentinel(directory, {}, heartbeat=Heartbeat(_et(10, 0, day=tomorrow)), day=tomorrow)
    (message,) = await next_day.check(_et(10, 0, 30, day=tomorrow))
    assert message[1].startswith("⛔ auto-fence ORB_V2: daily loss 2.00 R > 1.25 R")
    assert _rows(fences)["paper:ORB_V2"][:2] == (1, "sentinel: daily loss 2.00 R > 1.25 R (realized -$200.00)")


async def test_the_sentinel_never_resumes_and_never_writes_the_global_row(tmp_path):
    directory = tmp_path / "bridge"
    # Every sendable identity over its limit at once, and a losing streak on ORB_V2 too.
    _save(directory, MONDAY, [_closed("m", "ORB_V2", "-100.00", day=MONDAY)])
    _save(directory, TUESDAY, [_closed("t", "ORB_V2", "-100.00", day=TUESDAY)])
    _save(directory, TODAY, [
        _closed("w", "ORB_V2", "-130.00", day=TODAY),
        _bracket("g", "GAP_GO_V2", status="closed", realized_pnl="-200.00"),
        _bracket("v", "VWAP_PB_V2", status="closed", realized_pnl="-300.00"),
        _bracket("m", "MOMO_HOD_V2", status="closed", realized_pnl="-250.00"),
        _bracket("x", "MOMO_HOD_V1", status="closed", realized_pnl="-9999.00"),
    ], written_at=_et(11, 0))
    store = RecordingStore()
    memory: dict[str, str] = {}

    messages = await _sentinel(directory, memory, store=store, heartbeat=Heartbeat(_et(11, 0))).check(_et(11, 0, 5))

    # One stop per sendable identity, each with its identity; the daily loss wins
    # over ORB_V2's streak (one fence per identity per day).
    assert sorted(identity for identity, _reason in store.stops) == sorted(SENDABLE_IDENTITIES)
    assert dict(store.stops)["ORB_V2"].startswith("sentinel: daily loss 1.3")
    assert len(messages) == 4
    assert sorted(key for key in memory if key.startswith("sentinel_fence:")) == sorted(
        f"sentinel_fence:{name}" for name in SENDABLE_IDENTITIES
    )
    # The one write path refuses the global row and every name that is not sendable.
    for name in (None, "MOMO_HOD_V1", "orb_v2", ""):
        with pytest.raises(ValueError):
            await fence_identity(store, identity=name, reason="sentinel: x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await fence_identity(store, identity="ORB_V2", reason="owner wants it")
    # No resume anywhere in the module.
    assert ".resume(" not in inspect.getsource(sentinel)


async def test_an_identity_the_owner_already_stopped_keeps_its_reason(tmp_path, fences):
    directory = tmp_path / "bridge"
    _save(directory, TODAY, [_bracket("g", "GAP_GO_V2", status="closed", realized_pnl="-300.00")],
          written_at=_et(12, 0))
    await SQLitePaperKillStore().stop(reason="telegram /halt by owner", identity="GAP_GO_V2")

    (message,) = await _sentinel(directory, {}, heartbeat=Heartbeat(_et(12, 0))).check(_et(12, 0, 5))

    assert message[1] == (
        "⛔ auto-fence GAP_GO_V2: daily loss 3.00 R > 1.25 R (realized -$300.00) — new entries already"
        " blocked (fenced earlier; its reason is kept); resume: autotraderctl paper resume --identity GAP_GO_V2"
    )
    assert _rows(fences)["paper:GAP_GO_V2"][:2] == (1, "telegram /halt by owner")


async def test_the_check_and_the_stop_are_one_transaction_holding_the_write_lock(tmp_path, fences):
    """N15a LOW-5: while the auto-fence decides, the row is write-locked: an owner's
    /halt (or resume) from another connection cannot land between its check and
    its stop, so the Sentinel never overwrites a row stopped in that moment."""
    await SQLitePaperKillStore().resume(reason="owner: yesterday's check", identity="ORB_V2")
    tried: list[str] = []

    def owner_writes_meanwhile(row: FenceRow) -> bool:
        assert not row.stopped
        with sqlite3.connect(fences, timeout=0.05) as other:
            try:
                other.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                tried.append(str(exc))
            else:  # pragma: no cover - the defect: the lock was not held
                other.execute(
                    "UPDATE autotrader_execution_fences SET stopped=1, reason='telegram /halt by owner'"
                    " WHERE name='paper:ORB_V2'"
                )
                other.commit()
                tried.append("written")
        return False

    kept = await SQLitePaperKillStore().stop_identity(
        reason="sentinel: daily loss 2.00 R > 1.25 R (realized -$200.00)", identity="ORB_V2",
        unless=owner_writes_meanwhile,
    )

    assert kept is None and tried == ["database is locked"]
    assert _rows(fences)["paper:ORB_V2"][:2] == (1, "sentinel: daily loss 2.00 R > 1.25 R (realized -$200.00)")
    # A stopped row is returned as it is, never rewritten.
    assert (await SQLitePaperKillStore().stop_identity(reason="sentinel: x", identity="ORB_V2")) == FenceRow(
        True, "sentinel: daily loss 2.00 R > 1.25 R (realized -$200.00)", _rows(fences)["paper:ORB_V2"][2]
    )
    with pytest.raises(ValueError):
        await SQLitePaperKillStore().stop_identity(reason="sentinel: x", identity=None)  # type: ignore[arg-type]


async def test_an_owner_resume_holds_from_the_fence_table_when_the_notifier_state_is_lost(
    tmp_path, fences, monkeypatch
):
    """N15a LOW-5: the owner resumes today's auto-fence, then the notifier restarts
    with an unreadable state file (no memory): the fence table alone says the
    owner lifted today's fence, so the Sentinel never fences it again that day."""
    from trading.operations.notify_service import NotifyState
    from trading.storage import runtime_fence

    # The store stamps its rows with the wall clock: here the simulated session's.
    wall = {"at": _et(10, 0, 30)}
    monkeypatch.setattr(runtime_fence, "_now", lambda: wall["at"].isoformat())
    directory = tmp_path / "bridge"
    _save(directory, TODAY, [_bracket("orb", status="closed", realized_pnl="-130.00")], written_at=_et(10, 0))
    (message,) = await _sentinel(directory, {}, heartbeat=Heartbeat(_et(10, 0))).check(_et(10, 0, 30))
    assert message[1].startswith("⛔ auto-fence ORB_V2: daily loss 1.30 R")

    # 11:30, the owner's `paper resume --identity ORB_V2` (the CLI passes this annotate).
    wall["at"] = _et(11, 30)
    await SQLitePaperKillStore().resume(
        reason="owner: checked", identity="ORB_V2",
        annotate=lambda lifted: owner_resume_reason("owner: checked", lifted),
    )
    stopped, reason, _at = _rows(fences)["paper:ORB_V2"]
    assert (stopped, reason) == (0, "owner: checked (lifts sentinel fence of 2026-10-14)")

    # The notifier restarts on a corrupt state file: a fresh, empty memory.
    state_path = tmp_path / "notify" / "2026-10-14.json"
    state_path.parent.mkdir(mode=0o700)
    state_path.write_text("{not json", encoding="utf-8")
    state_path.chmod(0o600)
    memory = NotifyState.load(state_path, TODAY).alerts
    assert memory == {}
    restarted = _sentinel(directory, memory, heartbeat=Heartbeat(_et(12, 0)))
    assert await restarted.check(_et(12, 0, 5)) == []
    assert _rows(fences)["paper:ORB_V2"][:2] == (0, "owner: checked (lifts sentinel fence of 2026-10-14)")
    assert "sentinel_fence:ORB_V2" in memory  # remembered again: not even asked next check
    assert await restarted.check(_et(12, 1)) == []

    # The trace is today's only: the next session day fences on a new loss.
    tomorrow = date(2026, 10, 15)
    wall["at"] = _et(10, 0, 30, day=tomorrow)
    _save(directory, tomorrow, [_bracket("orb2", status="closed", realized_pnl="-200.00")],
          written_at=_et(10, 0, day=tomorrow))
    (fenced,) = await _sentinel(directory, {}, heartbeat=Heartbeat(_et(10, 0, day=tomorrow)), day=tomorrow).check(
        _et(10, 0, 30, day=tomorrow)
    )
    assert fenced[1].startswith("⛔ auto-fence ORB_V2: daily loss 2.00 R")


def test_the_resume_trace_names_the_lifted_fences_own_date_and_only_a_sentinel_fence():
    fence = FenceRow(True, "sentinel: losing streak 3 losing trades in a row", _et(15, 0, day=TUESDAY).isoformat())
    assert owner_resume_reason("ok", fence) == "ok (lifts sentinel fence of 2026-10-13)"
    # An owner's own stop, a row already resumed, or no row: the reason as typed.
    assert owner_resume_reason("ok", FenceRow(True, "telegram /halt by owner", fence.updated_at)) == "ok"
    assert owner_resume_reason("ok", FenceRow(False, fence.reason, fence.updated_at)) == "ok"
    assert owner_resume_reason("ok", None) == "ok"
    resumed_today = FenceRow(False, "ok (lifts sentinel fence of 2026-10-14)", _et(9, 0).isoformat())
    assert owner_resumed_on(resumed_today, TODAY)
    # Yesterday's fence lifted this morning: today's Sentinel still fences on a new loss.
    lifted_yesterday = FenceRow(False, "ok (lifts sentinel fence of 2026-10-13)", _et(9, 0).isoformat())
    assert not owner_resumed_on(lifted_yesterday, TODAY)
    # Resumed yesterday, or stopped again since: not today's resume.
    assert not owner_resumed_on(replace(resumed_today, updated_at=_et(23, 0, day=TUESDAY).isoformat()), TODAY)
    assert not owner_resumed_on(replace(resumed_today, stopped=True), TODAY)
    assert not owner_resumed_on(FenceRow(False, "owner: checked", _et(9, 0).isoformat()), TODAY)


async def test_a_fence_that_cannot_be_written_is_paged_and_retried(tmp_path):
    directory = tmp_path / "bridge"
    _save(directory, TODAY, [_bracket("o", status="closed", realized_pnl="-200.00")], written_at=_et(10, 0))

    class LockedStore(RecordingStore):
        locked = True

        async def stop_identity(self, *, reason: str, identity: str, unless=None) -> FenceRow | None:
            if self.locked:
                raise sqlite3.OperationalError("database is locked")
            return await super().stop_identity(reason=reason, identity=identity, unless=unless)

    store = LockedStore()
    memory: dict[str, str] = {}
    heartbeat = Heartbeat(_et(10, 0))
    watcher = _sentinel(directory, memory, store=store, heartbeat=heartbeat)

    (message,) = await watcher.check(_et(10, 0, 10))
    assert message[1] == (
        "⚠️ auto-fence ORB_V2 NOT written (OperationalError): daily loss 2.00 R > 1.25 R (realized -$200.00)"
        ' — block it by hand: autotraderctl paper stop --identity ORB_V2 --reason'
        ' "sentinel: daily loss 2.00 R > 1.25 R (realized -$200.00)"'
    )
    heartbeat.at = _et(10, 1)
    assert await watcher.check(_et(10, 1)) == []  # retried silently inside the half hour
    store.locked = False
    heartbeat.at = _et(10, 2)
    (fenced,) = await watcher.check(_et(10, 2))
    assert fenced[1].startswith("⛔ auto-fence ORB_V2: daily loss") and store.stops == [
        ("ORB_V2", "sentinel: daily loss 2.00 R > 1.25 R (realized -$200.00)")
    ]


# --------------------------------------------------------------------------
# Blindness: loud, and never a fence
# --------------------------------------------------------------------------


async def test_a_stale_bridge_state_is_blind_every_half_hour_and_fences_nothing(tmp_path, fences):
    directory = tmp_path / "bridge"
    # Real evidence of a loss, but nothing has been written (or ticked) since 09:40.
    _save(directory, TODAY, [_bracket("o", status="closed", realized_pnl="-300.00")], written_at=_et(9, 40))
    heartbeat = Heartbeat(_et(9, 40))
    memory: dict[str, str] = {}
    watcher = _sentinel(directory, memory, heartbeat=heartbeat)

    assert await watcher.check(_et(9, 46)) == [(
        "sentinel_blind",
        "⚠️ sentinel blind since 09:40 ET — no bridge update for 6 min (state file and heartbeat);"
        " no auto-fence while it cannot see",
    )]
    assert await watcher.check(_et(10, 15)) == []
    (again,) = await watcher.check(_et(10, 16))
    assert again[1].startswith("⚠️ sentinel blind since 09:40 ET — no bridge update for 36 min")
    assert _rows(fences) == {}  # blind: no fence, whatever the file says

    # The bridge ticks again: one "sees again" line, then the rule it could not apply.
    heartbeat.at = _et(10, 20)
    messages = await watcher.check(_et(10, 20, 5))
    assert [text for _alert, text in messages] == [
        "✅ sentinel sees the bridge state again (10:20 ET); auto-fence active",
        "⛔ auto-fence ORB_V2: daily loss 3.00 R > 1.25 R (realized -$300.00) — new entries blocked;"
        " resume: autotraderctl paper resume --identity ORB_V2",
    ]
    assert _rows(fences)["paper:ORB_V2"][0] == 1


async def test_a_quiet_day_is_not_blind_while_the_bridge_heartbeat_ticks(tmp_path):
    directory = tmp_path / "bridge"
    # The bridge rewrites its state only when a bracket changes: on a quiet day
    # the file is hours old and only the heartbeat row moves.
    _save(directory, TODAY, [], written_at=_et(9, 26))
    heartbeat = Heartbeat(_et(13, 59, 58))
    assert await _sentinel(directory, {}, heartbeat=heartbeat).check(_et(14, 0)) == []
    # Blind from exactly 5 minutes without either.
    heartbeat.at = _et(13, 55)
    assert await _sentinel(directory, {}, heartbeat=heartbeat).check(_et(13, 59, 59)) == []
    (blind,) = await _sentinel(directory, {}, heartbeat=heartbeat).check(_et(14, 0))
    assert blind[1].startswith("⚠️ sentinel blind since 13:55 ET — no bridge update for 5 min")
    # An unreadable heartbeat falls back to the file alone.
    heartbeat.raises = True
    (blind,) = await _sentinel(directory, {}, heartbeat=heartbeat).check(_et(14, 0))
    assert blind[1].startswith("⚠️ sentinel blind since 09:26 ET — no bridge update for 274 min")


async def test_an_unreadable_or_missing_state_is_blind_inside_the_bridge_window_only(tmp_path):
    directory = tmp_path / "bridge"
    alive = Heartbeat(_et(9, 31))
    # No file yet: the bridge saves after its connect, so 5 minutes of grace from 09:25.
    assert await _sentinel(directory, {}, heartbeat=alive).check(_et(9, 29)) == []
    assert await _sentinel(directory, {}, heartbeat=alive).check(_et(9, 31)) == [(
        "sentinel_blind",
        blind_text(_et(9, 25), "no bridge state file for today"),
    )]
    # Before the bridge's window and after it, nothing is judged.
    assert await _sentinel(directory, {}, heartbeat=alive).check(_et(9, 20)) == []
    assert await _sentinel(directory, {}, heartbeat=alive).check(_et(15, 56)) == []

    path = _save(directory, TODAY, [_bracket("o", status="closed", realized_pnl="-300.00")])
    path.write_text("{not json", encoding="utf-8")
    stamp = _et(10, 2).timestamp()
    os.utime(path, (stamp, stamp))
    assert read_bridge_state(path, TODAY).problem == "bridge state unreadable (JSONDecodeError)"
    (blind,) = await _sentinel(directory, {}, heartbeat=Heartbeat(_et(10, 3))).check(_et(10, 3))
    assert blind[1] == (
        "⚠️ sentinel blind since 10:02 ET — bridge state unreadable (JSONDecodeError);"
        " no auto-fence while it cannot see"
    )
    # Another session's document under today's name is not today's state.
    _save(directory, MONDAY, [])
    os.replace(directory / f"{MONDAY.isoformat()}.json", path)
    assert read_bridge_state(path, TODAY).problem == "bridge state holds another session"
    # A link is never followed.
    path.unlink()
    path.symlink_to(directory / "elsewhere.json")
    assert read_bridge_state(path, TODAY).problem == "bridge state is a symbolic link"


async def test_the_end_of_the_bridge_window_is_not_seeing_again(tmp_path):
    directory = tmp_path / "bridge"
    _save(directory, TODAY, [], written_at=_et(15, 40))
    memory: dict[str, str] = {}
    watcher = _sentinel(directory, memory, heartbeat=Heartbeat(_et(15, 40)))
    (blind,) = await watcher.check(_et(15, 50))
    assert blind[0] == "sentinel_blind"
    # 15:55: the bridge's window is over and nothing is judged any more; the
    # state is no fresher, so no "sees again" line.
    assert await watcher.check(_et(15, 55)) == []
    assert await watcher.check(_et(16, 5)) == []
