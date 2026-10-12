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
