from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from auth import get_current_user
from config import cfg
from db import retention
from models import User
from routers.admin_routes import router as admin_router


def _database_sidecars(db_path: Path) -> tuple[Path, Path]:
    return Path(f"{db_path}-wal"), Path(f"{db_path}-shm")


def _assert_database_absent(db_path: Path) -> None:
    wal_path, shm_path = _database_sidecars(db_path)
    assert not db_path.exists()
    assert not wal_path.exists()
    assert not shm_path.exists()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _test_user() -> User:
    return User(
        id="demo",
        email="demo@local",
        created_at=datetime.now(timezone.utc).isoformat(),
        settings={},
    )


def _admin_app(*, authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)
    if authenticated:
        app.dependency_overrides[get_current_user] = _test_user
    return app


def _assert_disabled_response(response) -> None:
    assert response.status_code == 503
    assert response.json() == {
        "error": retention.RETENTION_DISABLED_CODE,
        "detail": retention.RETENTION_DISABLED_DETAIL,
    }


def test_retention_policy_validation_remains_strict(tmp_path: Path):
    cfg.DB_PATH = str(tmp_path / "retention.db")

    with pytest.raises(ValueError, match="Unknown retention table"):
        retention.RetentionConfig({"unknown_table": 30})

    with pytest.raises(ValueError, match="Unsafe SQL table"):
        retention._safe_policy_parts(
            retention.RetentionPolicy(
                table="trades; DROP TABLE trades",
                timestamp_column="timestamp",
                retention_days=1,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_retention_service_fails_closed_without_artifacts(
    tmp_path: Path,
    dry_run: bool,
):
    db_path = tmp_path / "missing" / "retention.db"
    cfg.DB_PATH = str(db_path)

    with pytest.raises(
        retention.RetentionUnavailableError,
        match=retention.RETENTION_DISABLED_CODE,
    ):
        await retention.run_retention_cleanup(
            custom_policies={"alert_history": 1},
            dry_run=dry_run,
            vacuum=True,
        )

    _assert_database_absent(db_path)
    assert not db_path.parent.exists()


@pytest.mark.asyncio
async def test_retention_stats_fails_closed_without_artifacts(tmp_path: Path):
    db_path = tmp_path / "missing" / "retention.db"
    cfg.DB_PATH = str(db_path)

    with pytest.raises(
        retention.RetentionUnavailableError,
        match=retention.RETENTION_DISABLED_CODE,
    ):
        await retention.get_retention_stats()

    _assert_database_absent(db_path)
    assert not db_path.parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_retention_service_preserves_existing_database_bytes(
    tmp_path: Path,
    dry_run: bool,
):
    db_path = tmp_path / "retention.db"
    cfg.DB_PATH = str(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE sentinel (id TEXT PRIMARY KEY, value TEXT)")
        await db.execute("INSERT INTO sentinel VALUES ('one', 'preserve-me')")
        await db.commit()
    before = _sha256(db_path)

    with pytest.raises(retention.RetentionUnavailableError):
        await retention.run_retention_cleanup(dry_run=dry_run, vacuum=True)

    assert _sha256(db_path) == before
    wal_path, shm_path = _database_sidecars(db_path)
    assert not wal_path.exists()
    assert not shm_path.exists()


@pytest.mark.asyncio
async def test_private_retention_mutators_reject_before_file_or_database_access(
    tmp_path: Path,
):
    cfg.DB_PATH = str(tmp_path / "retention.db")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    jsonl_sentinel = backup_dir / "existing.jsonl"
    other_sentinel = backup_dir / "operator-note.txt"
    jsonl_sentinel.write_bytes(b'{"preserve": true}\n')
    other_sentinel.write_bytes(b"preserve this too\n")
    backup_hashes = {
        jsonl_sentinel: _sha256(jsonl_sentinel),
        other_sentinel: _sha256(other_sentinel),
    }

    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    parquet_sentinel = bars_dir / "old.parquet"
    parquet_sentinel.write_bytes(b"synthetic parquet sentinel")
    old_mtime = time.time() - (400 * 86400)
    os.utime(parquet_sentinel, (old_mtime, old_mtime))
    parquet_before = (_sha256(parquet_sentinel), parquet_sentinel.stat().st_mtime_ns)

    policy = retention.RetentionPolicy(
        table="alert_history",
        timestamp_column="fired_at",
        retention_days=1,
    )
    calls = (
        retention._backup_records(
            None,
            "alert_history",
            "fired_at",
            datetime.now(timezone.utc),
            backup_dir,
        ),
        retention._cleanup_table(None, policy, backup_dir, dry_run=False),
        retention._cleanup_parquet_files(
            data_dir=bars_dir,
            retention_days=1,
            dry_run=False,
        ),
        retention._vacuum_database(None),
    )
    for call in calls:
        with pytest.raises(retention.RetentionUnavailableError):
            await call

    assert {path: _sha256(path) for path in backup_hashes} == backup_hashes
    assert (_sha256(parquet_sentinel), parquet_sentinel.stat().st_mtime_ns) == parquet_before
    _assert_database_absent(Path(cfg.DB_PATH))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/api/admin/retention/cleanup", {"dry_run": True}),
        ("POST", "/api/admin/retention/cleanup", {"dry_run": False}),
        ("POST", "/api/admin/retention/cleanup-preview", None),
        ("GET", "/api/admin/retention/stats", None),
        ("DELETE", "/api/admin/retention/backups/arbitrary.bin", None),
    ],
)
async def test_authenticated_retention_operations_return_stable_503_without_artifacts(
    tmp_path: Path,
    method: str,
    path: str,
    payload: dict | None,
):
    db_path = tmp_path / "missing" / "retention.db"
    cfg.DB_PATH = str(db_path)
    transport = ASGITransport(app=_admin_app())
    kwargs = {"json": payload} if payload is not None else {}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path, **kwargs)

    _assert_disabled_response(response)
    _assert_database_absent(db_path)
    assert not db_path.parent.exists()


@pytest.mark.asyncio
async def test_backup_delete_preserves_jsonl_and_non_jsonl_sentinels(tmp_path: Path):
    db_path = tmp_path / "state" / "retention.db"
    cfg.DB_PATH = str(db_path)
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True)
    sentinels = [backup_dir / "archive.jsonl", backup_dir / "operator-note.txt"]
    sentinels[0].write_bytes(b'{"id": 1}\n')
    sentinels[1].write_bytes(b"do not delete\n")
    hashes = {path: _sha256(path) for path in sentinels}
    transport = ASGITransport(app=_admin_app())

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for sentinel in sentinels:
            response = await client.delete(
                f"/api/admin/retention/backups/{sentinel.name}"
            )
            _assert_disabled_response(response)

    assert {path: _sha256(path) for path in sentinels} == hashes
    _assert_database_absent(db_path)


@pytest.mark.asyncio
async def test_retention_policy_and_empty_backup_list_remain_read_only(tmp_path: Path):
    db_path = tmp_path / "missing" / "retention.db"
    cfg.DB_PATH = str(db_path)
    backup_dir = db_path.parent / "backups"
    transport = ASGITransport(app=_admin_app())

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        policies = await client.get("/api/admin/retention/policies")
        backups = await client.get("/api/admin/retention/backup-list")

    assert policies.status_code == 200
    assert len(policies.json()["policies"]) == len(retention.DEFAULT_RETENTION_DAYS)
    assert backups.status_code == 200
    assert backups.json()["backups"] == []
    _assert_database_absent(db_path)
    assert not backup_dir.exists()
    assert not db_path.parent.exists()


@pytest.mark.asyncio
async def test_retention_backup_list_does_not_mutate_archives(tmp_path: Path):
    db_path = tmp_path / "state" / "retention.db"
    cfg.DB_PATH = str(db_path)
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True)
    archive = backup_dir / "alert_history_20260714_120000.jsonl"
    other = backup_dir / "operator-note.txt"
    archive.write_bytes(b'{"id": "one"}\n')
    other.write_bytes(b"preserve\n")
    before = {path: _sha256(path) for path in (archive, other)}
    transport = ASGITransport(app=_admin_app())

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/admin/retention/backup-list")

    assert response.status_code == 200
    assert [item["filename"] for item in response.json()["backups"]] == [archive.name]
    assert {path: _sha256(path) for path in before} == before
    _assert_database_absent(db_path)


@pytest.mark.asyncio
async def test_retention_routes_still_require_authentication(tmp_path: Path):
    db_path = tmp_path / "missing" / "retention.db"
    cfg.DB_PATH = str(db_path)
    transport = ASGITransport(app=_admin_app(authenticated=False))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/admin/retention/cleanup",
            json={"dry_run": False},
        )

    assert response.status_code == 401
    _assert_database_absent(db_path)
    assert not db_path.parent.exists()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--execute"],
        ["--stats"],
        ["--execute", "--vacuum", "--retention-days", "1", "--table", "trades"],
    ],
)
def test_retention_cli_operations_fail_before_storage_access(tmp_path: Path, args: list[str]):
    db_path = tmp_path / "missing" / "retention.db"
    env = os.environ.copy()
    env["DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, "-m", "db.retention", *args],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert retention.RETENTION_DISABLED_CODE in result.stderr
    _assert_database_absent(db_path)
    assert not db_path.parent.exists()


def test_retention_cli_help_is_the_only_successful_mode(tmp_path: Path):
    db_path = tmp_path / "missing" / "retention.db"
    env = os.environ.copy()
    env["DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, "-m", "db.retention", "--help"],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Database retention operations (disabled by C1A)" in result.stdout
    _assert_database_absent(db_path)
    assert not db_path.parent.exists()


@pytest.mark.asyncio
async def test_diagnostics_news_refresh_preserves_old_rows(tmp_path: Path, monkeypatch):
    import diagnostics_service

    db_path = tmp_path / "diagnostics.db"
    cfg.DB_PATH = str(db_path)
    old_published_at = int(time.time()) - (30 * 86400)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE diag_news_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                headline TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                published_at INTEGER NOT NULL,
                fetched_at INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            "INSERT INTO diag_news_cache "
            "(source, headline, url, published_at, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("test", "old news", "https://example.invalid/old", old_published_at, old_published_at),
        )
        await db.commit()

    async def no_news() -> list[dict]:
        return []

    monkeypatch.setattr(diagnostics_service, "yahoo_news_rss", no_news)
    await diagnostics_service.DiagnosticsService()._refresh_news_cache()

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT headline, published_at FROM diag_news_cache"
        ) as cursor:
            rows = await cursor.fetchall()
    assert rows == [("old news", old_published_at)]


@pytest.mark.asyncio
async def test_candidate_expiration_preserves_all_terminal_history(tmp_path: Path):
    from db.direct_candidates import purge_expired_candidates

    db_path = tmp_path / "candidates.db"
    cfg.DB_PATH = str(db_path)
    old_queued_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE direct_candidates (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                payload TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                ttl_seconds INTEGER NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        await db.executemany(
            "INSERT INTO direct_candidates VALUES (?, 'demo', 'TEST', '{}', ?, 900, ?)",
            [
                ("terminal-applied", old_queued_at, "applied"),
                ("terminal-failed", old_queued_at, "failed"),
                ("terminal-expired", old_queued_at, "expired"),
                ("stale-queued", old_queued_at, "queued"),
            ],
        )
        await db.commit()

    expired_count = await purge_expired_candidates()

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT id, status FROM direct_candidates ORDER BY id"
        ) as cursor:
            rows = await cursor.fetchall()
    assert expired_count == 1
    assert rows == [
        ("stale-queued", "expired"),
        ("terminal-applied", "applied"),
        ("terminal-expired", "expired"),
        ("terminal-failed", "failed"),
    ]
