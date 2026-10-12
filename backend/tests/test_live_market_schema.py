"""Migration 11: universe and screener tables exist and accept share-class symbols."""
from __future__ import annotations

import json

import pytest

from trading.contracts import normalize_symbols


def test_share_class_symbols_use_ibkr_spacing():
    assert normalize_symbols(("brk b", "BF B")) == ("BRK B", "BF B")
    for bad in ("BRK  B", "BRK_B", ""):
        with pytest.raises(ValueError):
            normalize_symbols((bad,))


@pytest.mark.anyio
async def test_live_market_tables_are_created(tmp_path, monkeypatch):
    from config import cfg
    from db.core import get_db, init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "live.db"))
    await init_db()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO autotrader_universe VALUES (?,?,?,?,?,?,?,?,?)",
            ("watchlist", "BRK.B", "NYSE", "BRK B", 72063691, "NYSE", "resolved", None, "t"),
        )
        await db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            ("s1", "2026-10-12", "2026-10-12T14:00:00+00:00", json.dumps({"rows": []})),
        )
        await db.commit()
        async with db.execute("SELECT count(*) FROM autotrader_universe_daily") as cursor:
            assert (await cursor.fetchone())[0] == 0
