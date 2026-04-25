"""Data retention policy — automated cleanup for old database records and files.

Usage:
    # Run from CLI
    python -m db.retention --dry-run
    python -m db.retention --execute

    # Run from code
    from db.retention import run_retention_cleanup
    await run_retention_cleanup(dry_run=False)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import aiosqlite

from config import cfg
from db.core import get_db, transaction

log = logging.getLogger(__name__)

# Default retention periods (in days)
DEFAULT_RETENTION_DAYS = {
    "trades": 365 * 2,              # 2 years for trade history
    "backtests": 365,               # 1 year for backtest results
    "alert_history": 90,            # 90 days for alert firing history
    "ai_audit_log": 180,            # 6 months for AI audit trail
    "ai_shadow_decisions": 90,      # 90 days for shadow decisions
    "ai_parameter_snapshots": 30,   # 30 days for parameter snapshots
    "ai_rule_validation_runs": 180, # 6 months for validation runs
    "manual_interventions": 365,    # 1 year for interventions
    "regime_snapshots": 90,         # 90 days for regime data
    "diag_indicator_values": 90,    # 90 days for indicator values
    "diag_system_snapshots": 90,    # 90 days for system snapshots
    "diag_news_cache": 7,           # 7 days for news cache
    "diag_refresh_runs": 30,        # 30 days for refresh runs
    "ai_decision_runs": 180,        # 6 months for decision runs
    "ai_evaluation_runs": 90,       # 90 days for evaluation runs
    "direct_candidates": 7,           # 7 days for stale candidates
}

# Tables that support soft-delete (have a 'deleted_at' or 'archived' column)
# Currently none — we do hard deletes with optional backup


@dataclass
class RetentionPolicy:
    """Configuration for a single table's retention policy."""
    table: str
    timestamp_column: str
    retention_days: int
    backup_before_delete: bool = True
    extra_where: str | None = None  # Additional WHERE clause


class RetentionConfig:
    """Complete retention configuration."""
    
    def __init__(self, custom_policies: dict[str, int] | None = None):
        """Initialize with optional custom retention periods.
        
        Args:
            custom_policies: Dict mapping table name to retention days
        """
        self.policies: list[RetentionPolicy] = []
        self.backup_dir = Path(cfg.DB_PATH).parent / "backups"
        
        # Build policies from defaults + overrides
        retention_days = {**DEFAULT_RETENTION_DAYS, **(custom_policies or {})}
        
        # Define all retention policies
        self._add_policies(retention_days)
    
    def _add_policies(self, days: dict[str, int]) -> None:
        """Add all table retention policies."""
        
        # Core trading data
        self.policies.append(RetentionPolicy(
            table="trades",
            timestamp_column="timestamp",
            retention_days=days["trades"],
            backup_before_delete=True,
        ))
        
        self.policies.append(RetentionPolicy(
            table="backtests",
            timestamp_column="created_at",
            retention_days=days["backtests"],
            backup_before_delete=True,
        ))
        
        self.policies.append(RetentionPolicy(
            table="alert_history",
            timestamp_column="fired_at",
            retention_days=days["alert_history"],
            backup_before_delete=False,
        ))
        
        # AI/ML data
        self.policies.append(RetentionPolicy(
            table="ai_audit_log",
            timestamp_column="timestamp",
            retention_days=days["ai_audit_log"],
            backup_before_delete=True,
        ))
        
        self.policies.append(RetentionPolicy(
            table="ai_shadow_decisions",
            timestamp_column="timestamp",
            retention_days=days["ai_shadow_decisions"],
            backup_before_delete=False,
        ))
        
        self.policies.append(RetentionPolicy(
            table="ai_parameter_snapshots",
            timestamp_column="timestamp",
            retention_days=days["ai_parameter_snapshots"],
            backup_before_delete=False,
        ))
        
        self.policies.append(RetentionPolicy(
            table="ai_rule_validation_runs",
            timestamp_column="created_at",
            retention_days=days["ai_rule_validation_runs"],
            backup_before_delete=True,
        ))
        
        self.policies.append(RetentionPolicy(
            table="ai_decision_runs",
            timestamp_column="created_at",
            retention_days=days["ai_decision_runs"],
            backup_before_delete=True,
        ))
        
        self.policies.append(RetentionPolicy(
            table="ai_evaluation_runs",
            timestamp_column="created_at",
            retention_days=days["ai_evaluation_runs"],
            backup_before_delete=False,
        ))
        
        # Operations data
        self.policies.append(RetentionPolicy(
            table="manual_interventions",
            timestamp_column="opened_at",
            retention_days=days["manual_interventions"],
            backup_before_delete=True,
        ))
        
        self.policies.append(RetentionPolicy(
            table="regime_snapshots",
            timestamp_column="timestamp",
            retention_days=days["regime_snapshots"],
            backup_before_delete=False,
        ))
        
        # Diagnostics data
        self.policies.append(RetentionPolicy(
            table="diag_indicator_values",
            timestamp_column="created_at",
            retention_days=days["diag_indicator_values"],
            backup_before_delete=False,
        ))
        
        self.policies.append(RetentionPolicy(
            table="diag_system_snapshots",
            timestamp_column="created_at",
            retention_days=days["diag_system_snapshots"],
            backup_before_delete=False,
        ))
        
        self.policies.append(RetentionPolicy(
            table="diag_news_cache",
            timestamp_column="fetched_at",
            retention_days=days["diag_news_cache"],
            backup_before_delete=False,
        ))
        
        self.policies.append(RetentionPolicy(
            table="diag_refresh_runs",
            timestamp_column="started_at",
            retention_days=days["diag_refresh_runs"],
            backup_before_delete=False,
        ))
        
        # Direct candidates (use queued_at, also check status)
        self.policies.append(RetentionPolicy(
            table="direct_candidates",
            timestamp_column="queued_at",
            retention_days=days["direct_candidates"],
            backup_before_delete=False,
            extra_where="status IN ('completed', 'rejected', 'expired')",
        ))


@dataclass
class CleanupResult:
    """Result of a cleanup operation."""
    table: str
    rows_deleted: int
    backed_up: bool
    backup_path: Path | None
    error: str | None = None


async def _get_table_count(db: aiosqlite.Connection, table: str) -> int:
    """Get current row count for a table."""
    try:
        async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0
    except Exception as e:
        log.warning("Could not get count for %s: %s", table, e)
        return 0


async def _backup_records(
    db: aiosqlite.Connection,
    table: str,
    timestamp_column: str,
    cutoff: datetime,
    backup_dir: Path,
) -> Path | None:
    """Backup records to JSONL file before deletion."""
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{table}_{timestamp}.jsonl"
        
        # For tables with JSON data column, extract it; otherwise get all columns
        has_data_col = await _table_has_column(db, table, "data")
        
        if has_data_col:
            query = f"""
                SELECT id, {timestamp_column}, data 
                FROM {table} 
                WHERE {timestamp_column} < ?
            """
        else:
            query = f"""
                SELECT * FROM {table} 
                WHERE {timestamp_column} < ?
            """
        
        cutoff_str = cutoff.isoformat()
        records = []
        
        async with db.execute(query, (cutoff_str,)) as cur:
            rows = await cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            
            for row in rows:
                record = dict(zip(columns, row))
                # Parse JSON data column if present
                if "data" in record and isinstance(record["data"], str):
                    try:
                        record["data"] = json.loads(record["data"])
                    except json.JSONDecodeError:
                        pass
                records.append(record)
        
        # Write to JSONL
        with open(backup_path, "w") as f:
            for record in records:
                f.write(json.dumps(record, default=str) + "\n")
        
        log.info("Backed up %d records from %s to %s", len(records), table, backup_path)
        return backup_path
        
    except Exception as e:
        log.error("Failed to backup %s: %s", table, e)
        return None


async def _table_has_column(db: aiosqlite.Connection, table: str, column: str) -> bool:
    """Check if a table has a specific column."""
    try:
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
            return any(row[1] == column for row in rows)
    except Exception:
        return False


async def _cleanup_table(
    db: aiosqlite.Connection,
    policy: RetentionPolicy,
    backup_dir: Path,
    dry_run: bool = False,
) -> CleanupResult:
    """Clean up old records from a single table."""
    result = CleanupResult(
        table=policy.table,
        rows_deleted=0,
        backed_up=False,
        backup_path=None,
    )
    
    try:
        # Check if table exists
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (policy.table,)
        ) as cur:
            if not await cur.fetchone():
                log.debug("Table %s does not exist, skipping", policy.table)
                return result
        
        # Calculate cutoff date
        cutoff = datetime.now(timezone.utc) - timedelta(days=policy.retention_days)
        cutoff_str = cutoff.isoformat()
        
        # Count records to be deleted
        where_clause = f"{policy.timestamp_column} < ?"
        params = [cutoff_str]
        
        if policy.extra_where:
            where_clause += f" AND ({policy.extra_where})"
        
        count_query = f"SELECT COUNT(*) FROM {policy.table} WHERE {where_clause}"
        async with db.execute(count_query, params) as cur:
            row = await cur.fetchone()
            to_delete = row[0] if row else 0
        
        if to_delete == 0:
            log.debug("No old records to delete in %s", policy.table)
            return result
        
        log.info(
            "Found %d records in %s older than %s days (before %s)",
            to_delete, policy.table, policy.retention_days, cutoff_str[:10]
        )
        
        if dry_run:
            result.rows_deleted = to_delete
            return result
        
        # Backup before delete if configured
        if policy.backup_before_delete:
            backup_path = await _backup_records(
                db, policy.table, policy.timestamp_column, cutoff, backup_dir
            )
            if backup_path:
                result.backed_up = True
                result.backup_path = backup_path
        
        # Delete records
        delete_query = f"DELETE FROM {policy.table} WHERE {where_clause}"
        await db.execute(delete_query, params)
        
        result.rows_deleted = to_delete
        log.info("Deleted %d records from %s", to_delete, policy.table)
        
    except Exception as e:
        error_msg = str(e)
        log.error("Error cleaning up %s: %s", policy.table, error_msg)
        result.error = error_msg
    
    return result


async def _cleanup_parquet_files(
    data_dir: Path = Path("data/bars"),
    retention_days: int = 365,
    dry_run: bool = False,
) -> dict:
    """Clean up old Parquet files from data directory."""
    result = {
        "files_deleted": 0,
        "bytes_freed": 0,
        "errors": [],
    }
    
    if not data_dir.exists():
        return result
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    
    try:
        for file_path in data_dir.glob("*.parquet"):
            try:
                # Get file modification time
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                
                if mtime < cutoff:
                    size = file_path.stat().st_size
                    
                    if not dry_run:
                        file_path.unlink()
                    
                    result["files_deleted"] += 1
                    result["bytes_freed"] += size
                    log.info("Deleted old Parquet file: %s", file_path.name)
                    
            except Exception as e:
                result["errors"].append(f"{file_path.name}: {e}")
                log.warning("Could not process %s: %s", file_path, e)
                
    except Exception as e:
        result["errors"].append(str(e))
        log.error("Error cleaning up Parquet files: %s", e)
    
    return result


async def _vacuum_database(db: aiosqlite.Connection) -> None:
    """Run VACUUM to reclaim disk space after deletions."""
    try:
        log.info("Running VACUUM to reclaim disk space...")
        await db.execute("VACUUM")
        log.info("VACUUM completed")
    except Exception as e:
        log.warning("VACUUM failed: %s", e)


async def run_retention_cleanup(
    custom_policies: dict[str, int] | None = None,
    dry_run: bool = True,
    vacuum: bool = False,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict:
    """Run the complete retention cleanup process.
    
    Args:
        custom_policies: Override default retention periods {table: days}
        dry_run: If True, only report what would be deleted
        vacuum: If True, run VACUUM after cleanup
        progress_callback: Optional callback(table, current, total)
    
    Returns:
        Dict with cleanup statistics and results
    """
    config = RetentionConfig(custom_policies)
    results: list[CleanupResult] = []
    
    log.info("Starting retention cleanup (dry_run=%s)", dry_run)
    
    async with transaction() as db:
        total_tables = len(config.policies)
        
        for i, policy in enumerate(config.policies, 1):
            if progress_callback:
                progress_callback(policy.table, i, total_tables)
            
            result = await _cleanup_table(db, policy, config.backup_dir, dry_run)
            results.append(result)
        
        # Clean up Parquet files
        parquet_result = await _cleanup_parquet_files(dry_run=dry_run)
        
        # Vacuum if requested and not dry run
        if vacuum and not dry_run:
            await _vacuum_database(db)
    
    # Build summary
    total_deleted = sum(r.rows_deleted for r in results)
    total_backed_up = sum(1 for r in results if r.backed_up)
    errors = [r for r in results if r.error]
    
    summary = {
        "dry_run": dry_run,
        "tables_processed": len(results),
        "total_rows_deleted": total_deleted,
        "tables_backed_up": total_backed_up,
        "backup_directory": str(config.backup_dir),
        "parquet_cleanup": parquet_result,
        "errors": [
            {"table": r.table, "error": r.error} for r in errors
        ],
        "details": [
            {
                "table": r.table,
                "rows_deleted": r.rows_deleted,
                "backed_up": r.backed_up,
                "backup_path": str(r.backup_path) if r.backup_path else None,
            }
            for r in results
        ],
    }
    
    log.info(
        "Retention cleanup complete: %d rows deleted across %d tables",
        total_deleted, len(results)
    )
    
    return summary


async def get_retention_stats() -> dict:
    """Get current database size and record counts for retention planning."""
    db_path = Path(cfg.DB_PATH)
    
    stats = {
        "db_path": str(db_path),
        "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "table_counts": {},
        "retention_policies": DEFAULT_RETENTION_DAYS,
    }
    
    config = RetentionConfig()
    
    async with get_db() as db:
        for policy in config.policies:
            try:
                count = await _get_table_count(db, policy.table)
                stats["table_counts"][policy.table] = {
                    "current_rows": count,
                    "retention_days": policy.retention_days,
                }
            except Exception as e:
                stats["table_counts"][policy.table] = {"error": str(e)}
    
    return stats


def main() -> None:
    """CLI entry point for retention cleanup."""
    parser = argparse.ArgumentParser(description="Database retention cleanup")
    parser.add_argument(
        "--execute", "-e",
        action="store_true",
        help="Actually delete records (default is dry-run)"
    )
    parser.add_argument(
        "--vacuum", "-v",
        action="store_true",
        help="Run VACUUM after cleanup"
    )
    parser.add_argument(
        "--stats", "-s",
        action="store_true",
        help="Show current stats and exit"
    )
    parser.add_argument(
        "--retention-days", "-r",
        type=int,
        metavar="DAYS",
        help="Override all retention periods (use with caution)"
    )
    parser.add_argument(
        "--table",
        action="append",
        help="Specific table to clean (can be used multiple times)"
    )
    
    args = parser.parse_args()
    
    async def run():
        if args.stats:
            stats = await get_retention_stats()
            print(json.dumps(stats, indent=2))
            return
        
        # Build custom policies
        custom_policies = None
        if args.retention_days:
            custom_policies = {
                table: args.retention_days 
                for table in DEFAULT_RETENTION_DAYS.keys()
            }
        
        # Filter to specific tables if requested
        if args.table:
            if custom_policies is None:
                custom_policies = {}
            # Set retention to 0 for tables not in the list (skip them)
            for table in list(DEFAULT_RETENTION_DAYS.keys()):
                if table not in args.table:
                    custom_policies[table] = 99999  # Effectively skip
        
        def progress(table: str, current: int, total: int):
            print(f"  [{current}/{total}] Processing {table}...")
        
        summary = await run_retention_cleanup(
            custom_policies=custom_policies,
            dry_run=not args.execute,
            vacuum=args.vacuum,
            progress_callback=progress,
        )
        
        print("\n" + "=" * 60)
        print("RETENTION CLEANUP SUMMARY")
        print("=" * 60)
        print(f"Mode: {'EXECUTE' if args.execute else 'DRY RUN'}")
        print(f"Tables processed: {summary['tables_processed']}")
        print(f"Total rows to delete: {summary['total_rows_deleted']}")
        print(f"Tables backed up: {summary['tables_backed_up']}")
        print(f"Backup directory: {summary['backup_directory']}")
        
        if summary['parquet_cleanup']['files_deleted'] > 0:
            mb_freed = summary['parquet_cleanup']['bytes_freed'] / (1024 * 1024)
            print(f"Parquet files: {summary['parquet_cleanup']['files_deleted']} "
                  f"({mb_freed:.1f} MB)")
        
        if summary['errors']:
            print(f"\nErrors ({len(summary['errors'])}):")
            for err in summary['errors']:
                print(f"  - {err['table']}: {err['error']}")
        
        print("\nDetails:")
        for detail in summary['details']:
            if detail['rows_deleted'] > 0:
                print(f"  {detail['table']}: {detail['rows_deleted']} rows", end="")
                if detail['backed_up']:
                    print(" [backed up]")
                else:
                    print()
        
        if not args.execute:
            print("\n" + "=" * 60)
            print("This was a DRY RUN. No records were actually deleted.")
            print("Use --execute to perform the cleanup.")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
