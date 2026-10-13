"""Hot-list candidates come from the newest fresh screener snapshot + daily liquidity."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from trading.contracts import EngineMode, Run
from trading.orchestration.watchlist_source import HotListUnavailable, hotlist_candidates

NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)


def _row(symbol: str, con_id: int, *, hot: bool, rank: int | None) -> dict:
    return {"symbol": symbol, "con_id": con_id, "primary_exchange": "NASDAQ", "hot": hot, "hot_rank": rank}


async def _seed(observed_at: datetime) -> None:
    from db.core import get_db

    payload = {
        "schema": "screener.v1",
        "rows": [
            _row("NVDA", 1, hot=True, rank=2),
            _row("AMD", 2, hot=True, rank=1),
            _row("KO", 3, hot=False, rank=None),
            _row("BRK B", 4, hot=True, rank=3),  # not qualified today
        ],
    }
    async with get_db() as db:
        await db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            ("snap-1", "2026-10-12", observed_at.isoformat(), json.dumps(payload)),
        )
        for con_id, symbol, qualified in ((1, "NVDA", 1), (2, "AMD", 1), (3, "KO", 1), (4, "BRK B", 0)):
            await db.execute(
                "INSERT INTO autotrader_universe_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("2026-10-12", con_id, symbol, "100.00", "1000000", "100000000", None, qualified, None, "t"),
            )
        await db.commit()


def _run() -> Run:
    return Run(
        id="shadow-x-2026-10-12-momo1",
        owner_id="demo",
        source_sha="abcdef1",
        strategy_id="s",
        strategy_version=1,
        policy_fingerprints={},
        session_date=date(2026, 10, 12),
        mode=EngineMode.SHADOW,
    )


@pytest.mark.anyio
async def test_hot_rows_become_ranked_candidates_with_seeded_liquidity(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "w.db"))
    await init_db()
    await _seed(NOW - timedelta(minutes=1))

    scanner_run, candidates, liquidity = await hotlist_candidates(_run(), now=NOW)

    assert [c.symbol for c in candidates] == ["AMD", "NVDA"]
    assert [c.rank for c in candidates] == [1, 2]
    assert all(c.scanner_run_id == scanner_run.id for c in candidates)
    assert liquidity[2].average_dollar_volume == Decimal("100000000")
    assert scanner_run.parameters["source"] == "screener_hotlist"


@pytest.mark.anyio
async def test_stale_or_missing_snapshot_is_refused(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "w2.db"))
    await init_db()
    with pytest.raises(HotListUnavailable):
        await hotlist_candidates(_run(), now=NOW)
    await _seed(NOW - timedelta(minutes=30))
    with pytest.raises(HotListUnavailable, match="old"):
        await hotlist_candidates(_run(), now=NOW)


@pytest.mark.anyio
async def test_refresh_returns_only_newly_hot_rows_under_the_same_filters(tmp_path, monkeypatch):
    from config import cfg
    from db.core import get_db, init_db
    from trading.data import CandidateRepository, RunRepository, ScannerRunRepository
    from trading.orchestration.session_runner import ensure_orb_strategy
    from trading.orchestration.watchlist_source import hotlist_refresh

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "r.db"))
    await init_db()
    await _seed(NOW - timedelta(minutes=10))
    strategy = await ensure_orb_strategy(owner_id="demo")
    run = _run().model_copy(
        update={"strategy_id": strategy.strategy_id, "strategy_version": strategy.version}
    )
    # The run started on the first sweep: AMD and NVDA.
    scanner_run, opening, _ = await hotlist_candidates(run, now=NOW - timedelta(minutes=9))
    await RunRepository().save(run)
    await ScannerRunRepository().save(scanner_run)
    for candidate in opening:
        await CandidateRepository().save(candidate)
    # A later sweep: KO turns hot at the top; BRK B is hot but still unqualified.
    later = {
        "rows": [
            _row("KO", 3, hot=True, rank=1),
            _row("AMD", 2, hot=True, rank=2),
            _row("NVDA", 1, hot=True, rank=3),
            _row("BRK B", 4, hot=True, rank=4),
        ]
    }
    async with get_db() as db:
        await db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            ("snap-2", "2026-10-12", (NOW - timedelta(minutes=1)).isoformat(), json.dumps(later)),
        )
        await db.commit()

    added, liquidity = await hotlist_refresh(run, NOW)

    # Only the new symbol, in the run's own scanner run under its stable id;
    # the rank is provisional (the session assigns the next free one).
    assert [(c.symbol, c.id, c.scanner_run_id, c.rank) for c in added] == [
        ("KO", f"{run.id}:3", scanner_run.id, 1)
    ]
    assert liquidity[3].reference_price == Decimal("100.00")
    assert set(liquidity) == {3}
    with pytest.raises(HotListUnavailable, match="old"):
        await hotlist_refresh(run, NOW + timedelta(minutes=10))


@pytest.mark.anyio
async def test_gappers_use_gap_or_premarket_change_and_skip_scan_rows(tmp_path, monkeypatch):
    from config import cfg
    from db.core import get_db, init_db
    from trading.orchestration.watchlist_source import gap_candidates

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "g.db"))
    await init_db()
    rows = [
        {"symbol": "AAA", "con_id": 1, "source": "watchlist", "gap_pct": None, "change_pct": "4.5"},
        {"symbol": "BBB", "con_id": 2, "source": "watchlist", "gap_pct": "6.0", "change_pct": "1.0"},
        {"symbol": "CCC", "con_id": 3, "source": "watchlist", "gap_pct": "2.0", "change_pct": "2.5"},
        {"symbol": "DDD", "con_id": 4, "source": "ibkr_scan", "gap_pct": "9.0", "change_pct": "9.0"},
    ]
    async with get_db() as db:
        await db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            ("snap-g", "2026-10-12", (NOW - timedelta(minutes=1)).isoformat(), json.dumps({"rows": rows})),
        )
        for con_id in (1, 2, 3, 4):
            await db.execute(
                "INSERT INTO autotrader_universe_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("2026-10-12", con_id, f"S{con_id}", "50.00", "1000000", "90000000", None, 1, None, "t"),
            )
        await db.commit()

    scanner_run, candidates, liquidity = await gap_candidates(_run(), now=NOW)

    # Largest gap first; pre-market change stands in for a missing gap; scan rows excluded.
    assert [c.symbol for c in candidates] == ["BBB", "AAA"]
    assert scanner_run.parameters["source"] == "screener_gappers"
    assert set(liquidity) == {1, 2}
