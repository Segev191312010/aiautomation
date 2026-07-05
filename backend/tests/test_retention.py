from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from config import cfg
from db import retention


@pytest.fixture()
def retention_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "retention.db"
    cfg.DB_PATH = str(db_path)
    return db_path


async def _create_alert_history(db_path: Path) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE alert_history (id TEXT PRIMARY KEY, fired_at TEXT NOT NULL)"
        )
        await db.executemany(
            "INSERT INTO alert_history (id, fired_at) VALUES (?, ?)",
            [("old-alert", old), ("recent-alert", recent)],
        )
        await db.commit()


async def _alert_history_count(db_path: Path) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM alert_history") as cur:
            row = await cur.fetchone()
    return row[0]


@pytest.mark.asyncio
async def test_retention_rejects_non_allowlisted_table(retention_db: Path):
    async with aiosqlite.connect(retention_db) as db:
        result = await retention._cleanup_table(
            db,
            retention.RetentionPolicy(
                table="trades; DROP TABLE trades",
                timestamp_column="timestamp",
                retention_days=1,
            ),
            retention_db.parent / "backups",
            dry_run=True,
        )

    assert result.error is not None
    assert "Unsafe SQL table" in result.error

    with pytest.raises(ValueError, match="Unknown retention table"):
        retention.RetentionConfig({"unknown_table": 30})


@pytest.mark.asyncio
async def test_retention_dry_run_reports_without_deleting(retention_db: Path, monkeypatch):
    await _create_alert_history(retention_db)

    async def fake_parquet_cleanup(*args, **kwargs):
        return {"files_deleted": 0, "bytes_freed": 0, "errors": []}

    monkeypatch.setattr(retention, "_cleanup_parquet_files", fake_parquet_cleanup)

    summary = await retention.run_retention_cleanup(
        custom_policies={"alert_history": 30},
        dry_run=True,
    )

    detail = next(d for d in summary["details"] if d["table"] == "alert_history")
    assert detail["rows_deleted"] == 1
    assert summary["total_rows_deleted"] == 1
    assert await _alert_history_count(retention_db) == 2


@pytest.mark.asyncio
async def test_retention_execute_deletes_old_rows(retention_db: Path, monkeypatch):
    await _create_alert_history(retention_db)

    async def fake_parquet_cleanup(*args, **kwargs):
        return {"files_deleted": 0, "bytes_freed": 0, "errors": []}

    monkeypatch.setattr(retention, "_cleanup_parquet_files", fake_parquet_cleanup)

    summary = await retention.run_retention_cleanup(
        custom_policies={"alert_history": 30},
        dry_run=False,
    )

    detail = next(d for d in summary["details"] if d["table"] == "alert_history")
    assert detail["rows_deleted"] == 1
    assert summary["total_rows_deleted"] == 1
    assert await _alert_history_count(retention_db) == 1


@pytest.mark.asyncio
async def test_retention_stats_contract(retention_db: Path):
    await _create_alert_history(retention_db)

    stats = await retention.get_retention_stats()

    assert stats["db_path"] == str(retention_db)
    assert isinstance(stats["db_size_bytes"], int)
    assert set(retention.DEFAULT_RETENTION_DAYS).issubset(stats["retention_policies"])
    assert set(retention.DEFAULT_RETENTION_DAYS).issubset(stats["table_counts"])
    assert stats["table_counts"]["alert_history"] == {
        "current_rows": 2,
        "retention_days": retention.DEFAULT_RETENTION_DAYS["alert_history"],
    }
