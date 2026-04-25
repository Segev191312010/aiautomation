"""Admin routes — /api/admin

Maintenance and administrative operations including:
- Data retention cleanup
- Database statistics
- System health checks
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth import get_current_user
from db.retention import (
    run_retention_cleanup,
    get_retention_stats,
    DEFAULT_RETENTION_DAYS,
    CleanupResult,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────

class RetentionCleanupRequest(BaseModel):
    """Request body for retention cleanup."""
    dry_run: bool = Field(default=True, description="Preview only, don't delete")
    vacuum: bool = Field(default=False, description="Run VACUUM after cleanup")
    custom_policies: dict[str, int] | None = Field(
        default=None,
        description="Override retention days per table {table: days}"
    )


class TableCleanupDetail(BaseModel):
    """Detail for a single table's cleanup."""
    table: str
    rows_deleted: int
    backed_up: bool
    backup_path: str | None


class RetentionCleanupResponse(BaseModel):
    """Response from retention cleanup operation."""
    dry_run: bool
    tables_processed: int
    total_rows_deleted: int
    tables_backed_up: int
    backup_directory: str
    parquet_files_deleted: int
    parquet_bytes_freed: int
    errors: list[dict[str, str]]
    details: list[TableCleanupDetail]
    timestamp: datetime


class RetentionStatsResponse(BaseModel):
    """Response with current retention statistics."""
    db_path: str
    db_size_bytes: int
    db_size_human: str
    table_counts: dict[str, dict[str, Any]]
    retention_policies: dict[str, int]


class RetentionPolicyInfo(BaseModel):
    """Information about a retention policy."""
    table: str
    retention_days: int
    description: str


class RetentionPoliciesResponse(BaseModel):
    """Response with all retention policies."""
    policies: list[RetentionPolicyInfo]


# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _format_bytes(bytes_val: int) -> str:
    """Format bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.2f} TB"


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/retention/policies", response_model=RetentionPoliciesResponse)
async def get_retention_policies(user=Depends(get_current_user)):
    """Get all configured retention policies."""
    # Descriptions for each table type
    descriptions = {
        "trades": "Trade execution history and P&L",
        "backtests": "Backtest results and performance metrics",
        "alert_history": "Alert firing history",
        "ai_audit_log": "AI decision audit trail",
        "ai_shadow_decisions": "Shadow trading decisions",
        "ai_parameter_snapshots": "AI parameter snapshots",
        "ai_rule_validation_runs": "Rule validation run results",
        "manual_interventions": "Manual intervention records",
        "regime_snapshots": "Market regime snapshots",
        "diag_indicator_values": "Diagnostic indicator values",
        "diag_system_snapshots": "System diagnostic snapshots",
        "diag_news_cache": "Cached news articles",
        "diag_refresh_runs": "Data refresh run logs",
        "ai_decision_runs": "AI decision run logs",
        "ai_evaluation_runs": "AI evaluation run results",
        "direct_candidates": "Direct trade candidates",
    }
    
    policies = [
        RetentionPolicyInfo(
            table=table,
            retention_days=days,
            description=descriptions.get(table, "Data retention")
        )
        for table, days in DEFAULT_RETENTION_DAYS.items()
    ]
    
    return RetentionPoliciesResponse(policies=policies)


@router.get("/retention/stats", response_model=RetentionStatsResponse)
async def get_retention_statistics(user=Depends(get_current_user)):
    """Get current database statistics for retention planning."""
    stats = await get_retention_stats()
    
    return RetentionStatsResponse(
        db_path=stats["db_path"],
        db_size_bytes=stats["db_size_bytes"],
        db_size_human=_format_bytes(stats["db_size_bytes"]),
        table_counts=stats["table_counts"],
        retention_policies=stats["retention_policies"],
    )


@router.post("/retention/cleanup", response_model=RetentionCleanupResponse)
async def run_retention_cleanup_endpoint(
    request: RetentionCleanupRequest,
    user=Depends(get_current_user),
):
    """Run data retention cleanup.
    
    Use dry_run=True to preview what would be deleted.
    Use dry_run=False to actually perform deletions.
    """
    try:
        summary = await run_retention_cleanup(
            custom_policies=request.custom_policies,
            dry_run=request.dry_run,
            vacuum=request.vacuum,
        )
        
        # Convert details to Pydantic models
        details = [
            TableCleanupDetail(
                table=d["table"],
                rows_deleted=d["rows_deleted"],
                backed_up=d["backed_up"],
                backup_path=d["backup_path"],
            )
            for d in summary["details"]
        ]
        
        return RetentionCleanupResponse(
            dry_run=summary["dry_run"],
            tables_processed=summary["tables_processed"],
            total_rows_deleted=summary["total_rows_deleted"],
            tables_backed_up=summary["tables_backed_up"],
            backup_directory=summary["backup_directory"],
            parquet_files_deleted=summary["parquet_cleanup"]["files_deleted"],
            parquet_bytes_freed=summary["parquet_cleanup"]["bytes_freed"],
            errors=summary["errors"],
            details=details,
            timestamp=datetime.utcnow(),
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Retention cleanup failed: {str(e)}"
        )


@router.post("/retention/cleanup-preview")
async def preview_retention_cleanup(user=Depends(get_current_user)):
    """Preview what would be deleted in a retention cleanup (dry run)."""
    return await run_retention_cleanup_endpoint(
        RetentionCleanupRequest(dry_run=True),
        user
    )


@router.get("/retention/backup-list")
async def list_retention_backups(
    user=Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=100),
):
    """List available retention backup files."""
    from config import cfg
    from pathlib import Path
    
    backup_dir = Path(cfg.DB_PATH).parent / "backups"
    
    if not backup_dir.exists():
        return {"backups": [], "total_size": 0}
    
    backups = []
    total_size = 0
    
    for file_path in sorted(backup_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = file_path.stat()
        backups.append({
            "filename": file_path.name,
            "table": file_path.name.split("_")[0],
            "size_bytes": stat.st_size,
            "size_human": _format_bytes(stat.st_size),
            "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
        total_size += stat.st_size
    
    return {
        "backups": backups[:limit],
        "total_backups": len(backups),
        "total_size_bytes": total_size,
        "total_size_human": _format_bytes(total_size),
        "backup_directory": str(backup_dir),
    }


@router.delete("/retention/backups/{filename}")
async def delete_retention_backup(
    filename: str,
    user=Depends(get_current_user),
):
    """Delete a specific retention backup file."""
    from config import cfg
    from pathlib import Path
    
    backup_dir = Path(cfg.DB_PATH).parent / "backups"
    file_path = backup_dir / filename
    
    # Security check: ensure file is within backup directory
    try:
        file_path.resolve().relative_to(backup_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Backup file not found")
    
    try:
        file_path.unlink()
        return {"message": f"Deleted {filename}"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete backup: {str(e)}"
        )
